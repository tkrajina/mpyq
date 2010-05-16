#!/usr/bin/env python
# coding: utf-8

"""
mpyq is a Python library for reading MoPaQ archives.
"""

import cStringIO
import struct
import sys
import zlib
from collections import namedtuple


LANGUAGES = {
    0x000: 'English',
    0x404: 'Chinese (Taiwan)',
    0x405: 'Czech',
    0x407: 'German',
    0x409: 'English',
    0x40a: 'Spanish',
    0x40c: 'French',
    0x410: 'Italian',
    0x411: 'Japanese',
    0x412: 'Korean',
    0x415: 'Polish',
    0x416: 'Portuguese',
    0x419: 'Russian',
    0x809: 'English (UK)'
}

MPQ_FILE_IMPLODE        = 0x00000100
MPQ_FILE_COMPRESS       = 0x00000200
MPQ_FILE_ENCRYPTED      = 0x00010000
MPQ_FILE_FIX_KEY        = 0x00020000
MPQ_FILE_SINGLE_UNIT    = 0x01000000
MPQ_FILE_DELETE_MARKER  = 0x02000000
MPQ_FILE_SECTOR_CRC     = 0x04000000
MPQ_FILE_EXISTS         = 0x80000000

MPQFileHeader = namedtuple('MPQFileHeader',
    '''
    magic
    header_size
    arhive_size
    format_version
    sector_size_shift
    hash_table_offset
    block_table_offset
    hash_table_entries
    block_table_entries
    '''
)
MPQFileHeader.struct_format = '<4s2i2h4i'

MPQFileHeaderExt = namedtuple('MPQFileHeaderExt',
    '''
    extended_block_table_offset
    hash_table_offset_high
    block_table_offset_high
    '''
)
MPQFileHeaderExt.struct_format = 'q2h'

MPQUserDataHeader = namedtuple('MPQUserDataHeader',
    '''
    magic
    user_data_size
    mpq_header_offset
    user_data_header_size
    '''
)
MPQUserDataHeader.struct_format = '<4s3i'

MPQHashTableEntry = namedtuple('MPQHashTableEntry',
    '''
    hash_a
    hash_b
    locale
    platform
    block_table_index
    '''
)
MPQHashTableEntry.struct_format = '2I2HI'

MPQBlockTableEntry = namedtuple('MPQBlockTableEntry',
    '''
    offset
    archived_size
    size
    flags
    '''
)
MPQBlockTableEntry.struct_format = '4I'

SC2ReplayHeader = namedtuple('SC2ReplayHeader',
    '''
    identifier
    release_flag
    major_version
    minor_version
    maintenance_version
    build_number
    duration
    '''
)
SC2ReplayHeader.struct_format = '>x19s2x4bi6xhx'


class MPQArchive(object):

    def __init__(self, filename):
        self.file = open(filename, 'rb')
        self.header = self.read_header()
        self.hash_table =  self.read_table('hash')
        self.block_table = self.read_table('block')
        self.files = self.read_file('(listfile)').splitlines()

    def read_header(self):
        """Read the header of a MPQ archive."""

        def read_mpq_header(offset=None):
            if offset:
                self.file.seek(offset)
            data = self.file.read(32)
            header = MPQFileHeader._make(
                struct.unpack(MPQFileHeader.struct_format, data))
            header = header._asdict()
            if header['format_version'] == 1:
                data = self.file.read(12)
                extended_header = MPQFileHeaderExt._make(
                    struct.unpack(MPQFileHeaderExt.struct_format, data))
                header.update(extended_header._asdict())
            return header

        def read_mpq_user_data_header():
            data = self.file.read(16)
            header = MPQUserDataHeader._make(
                struct.unpack(MPQUserDataHeader.struct_format, data))
            header = header._asdict()
            header['content'] = self.file.read(header['user_data_header_size'])
            if header['content'].startswith('\x15StarCraft II replay'):
                header['starcraft2_replay_header'] = SC2ReplayHeader._make(
                    struct.unpack(SC2ReplayHeader.struct_format,
                                  header['content']))
            return header

        magic = self.file.read(4)
        self.file.seek(0)

        if magic == 'MPQ\x1a':
            header = read_mpq_header()
            header['offset'] = 0
        elif magic == 'MPQ\x1b':
            user_data_header = read_mpq_user_data_header()
            header = read_mpq_header(user_data_header['mpq_header_offset'])
            header['offset'] = user_data_header['mpq_header_offset']
            header['user_data_header'] = user_data_header

        return header

    def read_table(self, table_type):
        """Read either hash or block table of a MPQ archive."""

        table_offset = self.header['%s_table_offset' % table_type]
        table_size = self.header['%s_table_entries' % table_type] * 16
        key = self._hash('(%s table)' % table_type, 'TABLE')

        self.file.seek(table_offset + self.header['offset'])
        table = self.file.read(table_size)
        table = self._decrypt(table, key)
        return table

    def read_file(self, filename):
        """Read a file from the MPQ archive."""
        hash_a = self._hash(filename, 'HASH_A')
        hash_b = self._hash(filename, 'HASH_B')
        for i in range(self.header['hash_table_entries']):
            data = self.hash_table[i*16:i*16+16]
            entry = MPQHashTableEntry._make(
                struct.unpack(MPQHashTableEntry.struct_format, data))
            if entry.hash_a == hash_a and entry.hash_b == hash_b:
                break
        pos = entry.block_table_index * 16
        data = self.block_table[pos:pos+16]
        block_table_entry = MPQBlockTableEntry._make(
            struct.unpack(MPQBlockTableEntry.struct_format, data))
        if block_table_entry.flags & MPQ_FILE_EXISTS:
            offset = block_table_entry.offset + self.header['offset']
            self.file.seek(offset)
            file_data = self.file.read(block_table_entry.archived_size)
            if block_table_entry.flags & MPQ_FILE_COMPRESS:
                compression_type = ord(file_data[0])
                if compression_type == 2:
                    file_data = zlib.decompress(file_data[1:], 15)
            return file_data

    def extract(self):
        """Extract all the files inside MPQ archive in memory."""
        return dict((f, self.read_file(f)) for f in self.files)

    def _hash(self, string, hash_type):
        """Hash a string using MPQ's hash function."""
        hash_types = {
            'TABLE_OFFSET': 0,
            'HASH_A': 1,
            'HASH_B': 2,
            'TABLE': 3
        }
        seed1 = 0x7FED7FED
        seed2 = 0xEEEEEEEE

        for ch in string:
            ch = ord(ch.upper())
            value = self.encryption_table[(hash_types[hash_type] << 8) + ch]
            seed1 = (value ^ (seed1 + seed2)) & 0xFFFFFFFF
            seed2 = ch + seed1 + seed2 + (seed2 << 5) + 3 & 0xFFFFFFFF

        return seed1

    def _decrypt(self, data, key):
        """Decrypt hash and block table or blocks."""
        seed1 = key
        seed2 = 0xEEEEEEEE
        result = cStringIO.StringIO()

        for i in range(len(data) // 4):
            seed2 += self.encryption_table[0x400 + (seed1 & 0xFF)]
            seed2 &= 0xFFFFFFFF
            value = struct.unpack("<I", data[i*4:i*4+4])[0]
            value = (value ^ (seed1 + seed2)) & 0xFFFFFFFF

            seed1 = ((~seed1 << 0x15) + 0x11111111) | (seed1 >> 0x0B)
            seed1 &= 0xFFFFFFFF
            seed2 = value + seed2 + (seed2 << 5) + 3 & 0xFFFFFFFF

            result.write(struct.pack("<I", value))

        return result.getvalue()

    def _prepare_encryption_table():
        """Prepare encryption table for MPQ hash function."""
        seed = 0x00100001
        crypt_table = {}

        for i in range(256):
            index = i
            for j in range(5):
                seed = (seed * 125 + 3) % 0x2AAAAB
                temp1 = (seed & 0xFFFF) << 0x10;

                seed = (seed * 125 + 3) % 0x2AAAAB
                temp2 = (seed & 0xFFFF)

                crypt_table[index] = (temp1 | temp2)

                index += 0x100

        return crypt_table

    encryption_table = _prepare_encryption_table()


def main(argv):
    if len(argv) == 2:
        archive = MPQArchive(argv[1])
        print archive.header


if __name__ == '__main__':
    main(sys.argv)
