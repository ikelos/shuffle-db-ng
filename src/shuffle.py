import binascii
import collections
import hashlib
import os.path
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

import mutagen


# noinspection PyArgumentList
class Record(object):
    def __init__(self, parent):
        self.parent = parent
        self._struct = collections.OrderedDict([])
        self._fields = {}

    def __getitem__(self, item):
        if item not in list(self._struct.keys()):
            raise KeyError
        return self._fields.get(item, self._struct[item][1])

    def __setitem__(self, item, value):
        self._fields[item] = value

    def construct(self):
        output = b""
        for i in list(self._struct.keys()):
            (fmt, default) = self._struct[i]
            if fmt == "4s":
                fmt, default = "I", int(binascii.hexlify(default), 16)
            try:
                output += struct.pack("<" + fmt, self._fields.get(i, default))
            except TypeError:
                import pdb
                pdb.set_trace()
        return output

    def text_to_speech(self, text, dbid, playlist = False):
        # Create the voiceover wav file
        fn = "".join(["{0:02X}".format(x) for x in reversed(dbid)])
        path = os.path.join(self.base, "iPod_Control", "Speakable", "Tracks" if not playlist else "Playlists",
                            fn + ".wav")
        subprocess.call(["pico2wave", "-l", "en-GB", "-w", path, text])

    def path_to_ipod(self, filename):
        if os.path.commonprefix([os.path.abspath(filename), self.base]) != self.base:
            raise IOError("Cannot get Ipod filename, since file is outside the IPOD path")
        baselen = len(self.base)
        if self.base.endswith(os.path.sep):
            baselen -= 1
        ipodname = "/".join(os.path.abspath(filename)[baselen:].split(os.path.sep))
        return ipodname

    def ipod_to_path(self, ipodname):
        return os.path.abspath(os.path.join(self.base, os.path.sep.join(ipodname.split("/"))))

    @property
    def shuffledb(self):
        parent = self.parent
        while parent.__class__ != Shuffler:
            parent = parent.parent
        return parent

    @property
    def base(self):
        return self.shuffledb.base

    @property
    def tracks(self):
        return self.shuffledb.tracks

    @property
    def albums(self):
        return self.shuffledb.albums

    @property
    def artists(self):
        return self.shuffledb.artists

    @property
    def lists(self):
        return self.shuffledb.lists


# noinspection PyArgumentList
class TunesSD(Record):
    def __init__(self, parent):
        self.track_header = TrackHeader(self)
        self.play_header = PlaylistHeader(self)
        Record.__init__(self, parent)
        self._struct = collections.OrderedDict(
            [("header_id", ("4s", b"shdb")),
             ("unknown1", ("I", 0x02010001)),
             ("total_length", ("I", 64)),
             ("total_number_of_tracks", ("I", 0)),
             ("total_number_of_playlists", ("I", 0)),
             ("unknown2", ("Q", 0)),
             ("max_volume", ("B", 0)),
             ("voiceover_enabled", ("B", 1)),
             ("unknown3", ("H", 0)),
             ("total_tracks_without_podcasts", ("I", 0)),
             ("track_header_offset", ("I", 64)),
             ("playlist_header_offset", ("I", 0)),
             ("unknown4", ("20s", b"\x00" * 20)),
             ])

    def construct(self):
        # The header is a fixed length, so no need to precalculate it
        self.track_header.base_offset = 64
        track_header = self.track_header.construct()

        # The playlist offset will depend on the number of tracks
        self.play_header.base_offset = self.track_header.base_offset + len(track_header)
        play_header = self.play_header.construct()
        self["playlist_header_offset"] = self.play_header.base_offset

        self["total_number_of_tracks"] = self.track_header["number_of_tracks"]
        self["total_tracks_without_podcasts"] = self.track_header["number_of_tracks"]
        self["total_number_of_playlists"] = self.play_header["number_of_playlists"]

        output = Record.construct(self)
        return output + track_header + play_header


# noinspection PyArgumentList
class TrackHeader(Record):
    def __init__(self, parent):
        self.base_offset = 0
        Record.__init__(self, parent)
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"shth")),
            ("total_length", ("I", 0)),
            ("number_of_tracks", ("I", 0)),
            ("unknown1", ("Q", 0)),
        ])

    def construct(self):
        self["number_of_tracks"] = len(self.tracks)
        self["total_length"] = 20 + (len(self.tracks) * 4)
        output = Record.construct(self)

        # Construct the underlying tracks
        track_chunk = b""
        for i in self.tracks:
            track = Track(self)
            print("[*] Adding track", i)
            track.populate(i)
            output += struct.pack("I", self.base_offset + self["total_length"] + len(track_chunk))
            track_chunk += track.construct()
        return output + track_chunk


# noinspection PyArgumentList
class Track(Record):
    def __init__(self, parent):
        Record.__init__(self, parent)
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"shtr")),
            ("header_length", ("I", 0x174)),
            ("start_at_pos_ms", ("I", 0)),
            ("stop_at_pos_ms", ("I", 0)),
            ("volume_gain", ("I", 0)),
            ("filetype", ("I", 1)),
            ("filename", ("256s", b"\x00" * 256)),
            ("bookmark", ("I", 0)),
            ("dontskip", ("B", 1)),
            ("remember", ("B", 0)),
            ("unintalbum", ("B", 0)),
            ("unknown", ("B", 0)),
            ("pregap", ("I", 0x200)),
            ("postgap", ("I", 0x200)),
            ("numsamples", ("I", 0)),
            ("unknown2", ("I", 0)),
            ("gapless", ("I", 0)),
            ("unknown3", ("I", 0)),
            ("albumid", ("I", 0)),
            ("track", ("H", 1)),
            ("disc", ("H", 0)),
            ("unknown4", ("Q", 0)),
            ("dbid", ("8s", 0)),
            ("artistid", ("I", 0)),
            ("unknown5", ("32s", b"\x00" * 32)),
        ])

    def populate(self, filename):
        self["filename"] = self.path_to_ipod(filename)

        # Make the assumption that the FAT filesystem codepage is Latin-1
        # We therefore need to convert any UTF-8 filenames reported by dirwalk
        # into Latin-1 names
        self["filename"] = self["filename"].encode('latin-1')

        if os.path.splitext(filename)[1].lower() in (".m4a", ".m4b", ".m4p", ".aa"):
            self["filetype"] = 2

        text = os.path.splitext(os.path.basename(filename))[0]
        audio = mutagen.File(filename, easy = True)
        if audio:
            self["stop_at_pos_ms"] = int(audio.info.length * 1000)

            artist = audio.get("artist", ["Unknown"])[0]
            if artist in self.artists:
                self["artistid"] = self.artists.index(artist)
            else:
                self["artistid"] = len(self.artists)
                self.artists.append(artist)

            album = audio.get("album", ["Unknown"])[0]
            if album in self.albums:
                self["albumid"] = self.albums.index(album)
            else:
                self["albumid"] = len(self.albums)
                self.albums.append(album)

            if audio.get("title", "") and audio.get("artist", ""):
                text = " - ".join(audio.get("title", "") + audio.get("artist", ""))

        # Handle the VoiceOverData
        self["dbid"] = hashlib.md5(text.encode("ascii", "ignore")).digest()[:8]  # pylint: disable-msg=E1101
        self.text_to_speech(text, self["dbid"])


# noinspection PyArgumentList
class PlaylistHeader(Record):
    def __init__(self, parent):
        self.base_offset = 0
        Record.__init__(self, parent)
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"shph")),
            ("total_length", ("I", 0)),
            ("number_of_playlists", ("I", 0)),
            ("number_of_podcast_lists", ("I", 0xffffffff)),
            ("number_of_master_lists", ("I", 0)),
            ("number_of_audiobook_lists", ("I", 0xffffffff)),
            ("unknown1", ("I", 0)),
            ("unknown2", ("I", 0xffffffff)),
            ("unknown3", ("I", 0)),
            ("unknown4", ("I", 0xffffffff)),
            ("unknown5", ("I", 0)),
            ("unknown6", ("I", 0xffffffff)),
            ("unknown7", ("20s", b"\x00" * 20)),
        ])

    def construct(self):  # pylint: disable-msg=W0221
        # Build the master list
        masterlist = Playlist(self)
        print("[+] Adding master playlist")
        masterlist.set_master(self.tracks)
        chunks = [masterlist.construct()]

        # Build all the remaining playlists
        playlistcount = 1
        for i in self.lists:
            playlist = Playlist(self)
            print("[+] Adding playlist", i)
            playlist.populate(i)
            construction = playlist.construct()
            if playlist["number_of_songs"] > 0:
                playlistcount += 1
                chunks += [construction]

        self["number_of_playlists"] = playlistcount
        self["number_of_master_lists"] = 0
        self["total_length"] = 0x44 + (self["number_of_playlists"] * 4)
        # Start the header

        output = Record.construct(self)
        offset = self.base_offset + self["total_length"]

        for i in range(len(chunks)):
            output += struct.pack("I", offset)
            offset += len(chunks[i])

        return output + b"".join(chunks)


# noinspection PyArgumentList
class Playlist(Record):
    def __init__(self, parent):
        self.listtracks = []
        Record.__init__(self, parent)
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"shpl")),
            ("total_length", ("I", 0)),
            ("number_of_songs", ("I", 0)),
            ("number_of_nonaudio", ("I", 0)),
            ("dbid", ("8s", b"\x00" * 8)),
            ("listtype", ("I", 2)),
            ("unknown1", ("16s", b"\x00" * 16))
        ])

    def set_master(self, tracks):
        self["dbid"] = hashlib.md5(b"masterlist").digest()[:8]  # pylint: disable-msg=E1101
        self["listtype"] = 1
        self.text_to_speech("All songs", self["dbid"], True)
        self.listtracks = tracks

    def populate_m3u(self, data):
        listtracks = []
        for i in data:
            if not i.startswith("#"):
                path = i.strip()
                listtracks.append(path)
        return listtracks

    def populate_pls(self, data):
        sorttracks = []
        for i in data:
            dataarr = i.strip().split("=")
            if dataarr[0].lower().startswith("file"):
                num = int(dataarr[0][4:])
                filename = urllib.parse.unquote(dataarr[1]).strip()
                if filename.lower().startswith('file://'):
                    filename = filename[7:]
                sorttracks.append((num, filename))
        listtracks = [x for (_, x) in sorted(sorttracks)]
        return listtracks

    def remove_relatives(self, relative, filename):
        base = os.path.dirname(os.path.abspath(filename))
        if not os.path.exists(relative):
            relative = os.path.join(base, relative)
        return relative

    def populate(self, filename):
        f = open(filename, "r")
        data = f.readlines()
        f.close()

        extension = os.path.splitext(filename)[1].lower()
        if extension == '.pls':
            self.listtracks = self.populate_pls(data)
        elif extension == '.m3u':
            self.listtracks = self.populate_m3u(data)
        # Ensure all paths are not relative to the playlist file
        for i in range(len(self.listtracks)):
            self.listtracks[i] = self.remove_relatives(self.listtracks[i], filename)

        # Handle the VoiceOverData
        text = os.path.splitext(os.path.basename(filename))[0]
        self["dbid"] = hashlib.md5(bytes(text, "utf-8")).digest()[:8]  # pylint: disable-msg=E1101
        self.text_to_speech(text, self["dbid"], True)

    def construct(self):  # pylint: disable-msg=W0221
        self["total_length"] = 44 + (4 * len(self.listtracks))
        self["number_of_songs"] = 0

        chunks = b""
        for i in self.listtracks:
            position = self.tracks.index(self.ipod_to_path(i))
            if position > -1:
                chunks += struct.pack("I", position)
                self["number_of_songs"] += 1
        self["number_of_nonaudio"] = self["number_of_songs"]

        output = Record.construct(self)
        return output + chunks


class Shuffler(object):
    def __init__(self, path):
        self.path, self.base = self.determine_base(path)
        self.tracks = []
        self.albums = []
        self.artists = []
        self.lists = []
        self.tunessd = None

    def dump_state(self):
        print("Shuffle DB state")
        print("Tracks", self.tracks)
        print("Albums", self.albums)
        print("Artists", self.artists)
        print("Playlists", self.lists)

    def determine_base(self, path):
        base = os.path.abspath(path)
        while not os.path.ismount(base):
            base = os.path.dirname(base)
        return path, base

    def populate(self):
        self.tunessd = TunesSD(self)
        for (dirpath, dirnames, filenames) in os.walk(self.path):
            dirnames.sort()
            # Ignore the speakable directory and any hidden directories
            if "ipod_control/speakable" not in dirpath.lower() and "/." not in dirpath.lower():
                for filename in sorted(filenames, key = lambda x: x.lower()):
                    if os.path.splitext(filename)[1].lower() in (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav"):
                        self.tracks.append(os.path.abspath(os.path.join(dirpath, filename)))
                    if os.path.splitext(filename)[1].lower() in (".pls", ".m3u"):
                        self.lists.append(os.path.abspath(os.path.join(dirpath, filename)))

    def write_database(self):
        f = open(os.path.join(self.base, "iPod_Control", "iTunes", "iTunesSD"), "wb")
        f.write(self.tunessd.construct())
        f.close()


#
# Read all files from the directory
# Construct the appropriate iTunesDB file
# Construct the appropriate iTunesSD file
#   http://shuffle3db.wikispaces.com/iTunesSD3gen
# Use festival to produce voiceover data
#

if __name__ == '__main__':
    shuffle = Shuffler(sys.argv[1])
    shuffle.populate()
    shuffle.write_database()
