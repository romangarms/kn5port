"""FH1's English string tables (media/stringtables/EN.zip, LSB2 files, UTF-16 big endian). The database refers to a string as `_&<id>`: the id's low 16 bits
are the string's key in its table, the high 16 bits name the table."""
import struct, zipfile
import gamepath, gamezip


def read(table, dlc=False):
    """{key: text} for one table, such as 'Events.str'. dlc: the track's DLC copy of it, which holds the DLC's strings"""
    zp, name = (gamepath.DLC + '/DLCZips/StringTables_pri_1750.zip', 'EN/' + table) if dlc and gamepath.DLC else (gamezip.MEDIA + 'stringtables/EN.zip', table)
    z = zipfile.ZipFile(zp)
    b = z.read(name) if z.getinfo(name).compress_type == 0 else gamezip.read(zp, name)
    n = struct.unpack('>I', b[40:44])[0]; rows = [struct.unpack('>HI', b[44 + 6 * i:50 + 6 * i]) for i in range(n)]
    text = b[50 + 6 * n:]          # after the rows, a 0xffff row holding the text's length in characters
    out = {}
    for key, at in rows:
        end = at
        while text[2 * end:2 * end + 2] != b'\0\0':
            end += 1
        out[key] = text[2 * at:2 * end].decode('utf-16be')
    return out


def lookup(table, ref):
    """the text for a database `_&<id>` reference, or '' """
    return table.get(int(ref[2:]) & 0xffff, '') if isinstance(ref, str) and ref.startswith('_&') else ''
