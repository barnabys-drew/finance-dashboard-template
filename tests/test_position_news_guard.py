"""fetch_yahoo_rss must refuse feeds that declare XML entities."""
import io
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import position_news

BOMB = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        b'<rss><channel><item><title>&a;</title><link>https://e.com</link></item></channel></rss>')
PLAIN = (b'<?xml version="1.0"?><rss><channel><item><title>T</title>'
         b'<link>https://e.com/a</link></item></channel></rss>')


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class EntityGuardTests(unittest.TestCase):
    def test_entity_declaration_is_refused(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(BOMB)):
            with self.assertRaises(ET.ParseError):
                position_news.fetch_yahoo_rss("AAPL")

    def test_plain_feed_still_parses(self):
        with mock.patch("urllib.request.urlopen", return_value=_Resp(PLAIN)):
            stories = position_news.fetch_yahoo_rss("AAPL")
        self.assertEqual([s["title"] for s in stories], ["T"])


if __name__ == "__main__":
    unittest.main()
