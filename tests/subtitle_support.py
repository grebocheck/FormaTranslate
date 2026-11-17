"""Synthetic caption sources shared by workflow and interface tests."""
from __future__ import annotations

import codecs

CASES = [
    (".srt", 11, "42\r\n00:00:01,250 --> 00:00:04,500\r\n<i>Hello world</i> 25 kW.\r\nSecond caption line.\r\n\r\n"
     "50\r\n00:00:05,000 --> 00:00:08,000\r\n<font face=\"PRIVATE_FONT\" color=\"#FFFFFF\">Hello again.</font>\r\n"),
    (".vtt", 12, "WEBVTT PRIVATE_HEADER\r\n\r\n"
     "STYLE\r\n::cue(.PRIVATE_CLASS) { color: lime; }\r\n\r\n"
     "REGION\r\nid:PRIVATE_REGION\r\nwidth:40%\r\nlines:3\r\n\r\n"
     "NOTE PRIVATE_NOTE\r\nPrivate comment text.\r\n\r\n"
     "PRIVATE_ID\r\n00:01.250 --> 00:04.500 align:start region:PRIVATE_REGION\r\n"
     "<v PRIVATE_VOICE><c.PRIVATE_CLASS><i>Hello world</i> 25 kW.</c>\r\nSecond caption line.</v>\r\n\r\n"
     "PRIVATE_NEXT_ID\r\n00:05.000 --> 00:08.000\r\n<lang en-US>Hello again.</lang>\r\n"),
]


def write_source(tmp_path, suffix, text):
    source = tmp_path / ("source" + suffix)
    source.write_bytes(codecs.BOM_UTF8 + text.encode("utf-8"))
    return source
