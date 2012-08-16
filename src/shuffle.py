
import sys
import hashlib
import binascii
import struct
import os.path
import mutagen
import subprocess
import collections

class Record(object):

    def __init__(self):
        self.struct = collections.OrderedDict([])
        self.fields = {}

    def construct(self):
        output = ""
        for i in self.struct:
            (fmt, default) = self.struct[i]
            if fmt == "4s":
                fmt, default = "I", int(binascii.hexlify(default), 16)
            output += struct.pack("<" + fmt, self.fields.get(i, default))
        return output

class TunesSD(Record):
    def __init__(self, base = None):
        self.base = base
        self.track_header = TrackHeader()
        self.play_header = PlaylistHeader()
        Record.__init__(self)
        self.struct = collections.OrderedDict([
                           ("header_id", ("4s", "shdb")),
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
                           ("unknown4", ("20s", "\x00" * 20)),
                                               ])

    def construct(self):
        # The header is a fixed length, so no need to precalculate it
        self.track_header.base_offset = 64
        track_header = self.track_header.construct()
        # The playlist offset will depend on the number of tracks

        self.play_header.base_offset = len(track_header) + self.track_header.base_offset
        self.play_header.create_master_list(range(len(self.track_header.tracks)))
        play_header = self.play_header.construct()
        self.fields["playlist_header_offset"] = self.play_header.base_offset

        self.fields["total_number_of_tracks"] = self.track_header.fields["number_of_tracks"]
        self.fields["total_tracks_without_podcasts"] = self.track_header.fields["number_of_tracks"]
        self.fields["total_number_of_playlists"] = self.play_header.fields["number_of_playlists"]

        output = Record.construct(self)
        return output + track_header + play_header

    def add_track(self, filename):
        self.track_header.add(self.base, os.path.abspath(filename))

    def add_playlist(self, filename):
        self.play_header.add(self.base, os.path.abspath(filename))

class TrackHeader(Record):
    def __init__(self):
        self.tracks = []
        self.base_offset = 0
        Record.__init__(self)
        self.struct = collections.OrderedDict([
                           ("header_id", ("4s", "shth")),
                           ("total_length", ("I", 0)),
                           ("number_of_tracks", ("I", 0)),
                           ("unknown1", ("Q", 0)),
                                             ])

    def construct(self):
        self.fields["number_of_tracks"] = len(self.tracks)
        self.fields["total_length"] = 20 + (len(self.tracks) * 4)
        output = Record.construct(self)
        track_chunk = ""
        for i in self.tracks:
            output += struct.pack("I", self.base_offset + self.fields["total_length"] + len(track_chunk))
            track_chunk += i.construct()
        return output + track_chunk

    def add(self, base, filename):
        track = Track()
        print "[*] Adding song", filename
        track.populate(base, filename)
        self.tracks.append(track)

class Track(Record):

    albums = []
    artists = []

    def __init__(self):
        Record.__init__(self)
        self.struct = collections.OrderedDict([
                           ("header_id", ("4s", "shtr")),
                           ("header_length", ("I", 0x174)),
                           ("start_at_pos_ms", ("I", 0)),
                           ("stop_at_pos_ms", ("I", 0)),
                           ("volume_gain", ("I", 0)),
                           ("filetype", ("I", 1)),
                           ("filename", ("256s", "\x00" * 256)),
                           ("bookmark", ("I", 0)),
                           ("dontskip", ("B", 1)),
                           ("remember", ("B", 0)),
                           ("unintalbum", ("B", 0)),
                           ("unknown", ("B", 0)),
                           ("pregap", ("I", 0x210)),
                           ("postgap", ("I", 0x3e4)),
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
                           ("unknown5", ("32s", "\x00" * 32)),
                           ])

    def populate(self, base, filename):
        audio = mutagen.File(filename, easy = True)
        self.fields["stop_at_pos_ms"] = int(audio.info.length * 1000)
        self.fields["filename"] = filename[len(base):] + ("\x00" * (256 - len(filename) + len(base)))

        if os.path.splitext(filename)[1].lower() in (".m4a", ".m4b", ".m4p", ".aa"):
            self.fields["filetype"] = 2

        artist = audio.get("artist", [u"Unknown"])[0]
        if artist in self.artists:
            self.fields["artistid"] = self.artists.index(artist)
        else:
            self.fields["artistid"] = len(self.artists)
            self.artists.append(artist)

        album = audio.get("album", [u"Unknown"])[0]
        if album in self.albums:
            self.fields["albumid"] = self.albums.index(album)
        else:
            self.fields["albumid"] = len(self.albums)
            self.albums.append(album)

        self.fields["dbid"] = hashlib.md5(self.fields["filename"]).digest()[:8] #pylint: disable-msg=E1101
        flitefn = "".join([ "{0:02X}".format(ord(i)) for i in reversed(self.fields["dbid"])])
        text = " - ".join(audio.get("title", "") + audio.get("artist", ""))
        if not text:
            text = os.path.splitext(os.path.basename(filename))[0]
        subprocess.call(["flite", "-voice", "rms", text, os.path.join(base, "iPod_Control", "Speakable", "Tracks", flitefn + ".wav")])
        # Create the voiceover wav file

class PlaylistHeader(Record):
    def __init__(self):
        self.lists = []
        self.masterlist = None
        self.base_offset = 0
        Record.__init__(self)
        self.struct = collections.OrderedDict([
                          ("header_id", ("4s", "shph")),
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
                          ("unknown7", ("20s", "\x00" * 20)),
                                              ])

    def construct(self):
        self.fields["number_of_playlists"] = len(self.lists) + 1
        self.fields["number_of_master_lists"] = 0
        self.fields["total_length"] = 0x44 + (self.fields["number_of_playlists"] * 4)
        output = Record.construct(self)
        offset = self.base_offset + self.fields["total_length"]
        output += struct.pack("I", offset)
        playlist_chunk = self.masterlist.construct()
        for i in self.lists:
            output += struct.pack("I", offset)
            playlist_chunk += i.construct()
            offset += len(playlist_chunk)
        return output + playlist_chunk

    def create_master_list(self, tracks):
        self.masterlist = Playlist()
        # self.masterlist.fields["dbid"] = hashlib.md5("masterlist").digest()[:8] #pylint: disable-msg=E1101
        self.masterlist.fields["listtype"] = 1
        self.masterlist.fields["number_of_songs"] = len(tracks)
        self.masterlist.fields["number_of_nonaudio"] = len(tracks)
        self.masterlist.tracks = tracks

    def add(self, base, filename):
        playlist = Playlist()
        playlist.populate(base, filename)
        self.lists.append(playlist)

class Playlist(Record):
    def __init__(self):
        self.tracks = []
        Record.__init__(self)
        self.struct = collections.OrderedDict([
                          ("header_id", ("4s", "shpl")),
                          ("total_length", ("I", 0)),
                          ("number_of_songs", ("I", 0)),
                          ("number_of_nonaudio", ("I", 0)),
                          ("dbid", ("8s", "\x00" * 8)),
                          ("listtype", ("I", 2)),
                          ("unknown1", ("16s", "\x00" * 16))
                                              ])

    def populate(self, base, filename):
        f = open(filename, "rb")
        self.tracks = f.read().split("\n")
        self.fields["dbid"] = hashlib.md5(filename[base:]).digest()[:8] #pylint: disable-msg=E1101
        f.close()

    def construct(self):
        self.fields["total_length"] = 44 + (4 * len(self.tracks))
        output = Record.construct(self)
        for i in self.tracks:
            output += struct.pack("I", i)
        return output

class Shuffler(object):
    def __init__(self, path):
        self.path, self.base = self.determine_base(path)
        print self.base
        self.db = None

    def determine_base(self, path):
        base = os.path.abspath(path)
        while not os.path.ismount(base):
            base = os.path.dirname(base)
        return path, base

    def populate(self):
        self.db = TunesSD(self.base)
        for (dirpath, _dirnames, filenames) in os.walk(self.path, False):
            if "ipod_control/speakable" not in dirpath.lower():
                for filename in filenames:
                    if os.path.splitext(filename)[1].lower() in (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav"):
                        self.db.add_track(os.path.join(dirpath, filename))
                    if os.path.splitext(filename)[1].lower() in (".pls",):
                        self.db.add_playlist(os.path.join(dirpath, filename))

    def write_database(self):
        f = open(os.path.join(self.base, "iPod_Control", "iTunes", "iTunesSD"), "wb")
        f.write(self.db.construct())
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
