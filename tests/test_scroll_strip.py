import unittest

from phone_harness.scroll_strip import enumerate_scroll_strip


class ScrollStripTests(unittest.TestCase):
    def test_dedupes_overlapping_pages_and_collects_all_customers(self):
        pages = [
            [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            [{"id": "c"}, {"id": "d"}, {"id": "e"}],
            [{"id": "e"}, {"id": "f"}],
        ]
        index = 0

        def capture():
            return pages[index]

        def scroll():
            nonlocal index
            if index + 1 >= len(pages):
                return False
            index += 1
            return True

        result = enumerate_scroll_strip(
            capture, scroll, key_fn=lambda item: item["id"]
        )
        self.assertEqual(result["unique_count"], 6)
        self.assertEqual([item["id"] for item in result["items"]], list("abcdef"))

    def test_stops_after_repeated_page_without_new_items(self):
        page = [{"id": "a"}, {"id": "b"}]
        scroll_count = 0

        def scroll():
            nonlocal scroll_count
            scroll_count += 1
            return True

        result = enumerate_scroll_strip(
            lambda: page,
            scroll,
            key_fn=lambda item: item["id"],
            max_pages=10,
            stop_after_stagnant_pages=2,
        )
        self.assertTrue(result["stopped_stagnant"])
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(scroll_count, 2)

    def test_max_pages_is_a_hard_bound(self):
        counter = 0

        def capture():
            nonlocal counter
            counter += 1
            return [{"id": counter}]

        result = enumerate_scroll_strip(
            capture,
            lambda: True,
            key_fn=lambda item: item["id"],
            max_pages=4,
        )
        self.assertEqual(result["page_count"], 4)
        self.assertEqual(result["unique_count"], 4)


if __name__ == "__main__":
    unittest.main()
