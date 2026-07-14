import os
import tempfile
import unittest

from video_saver_node import _expand_digit_batch_paths, _parse_batch_timestamp


class VideoSaverBatchTests(unittest.TestCase):
    def test_parse_dance_timestamp(self):
        self.assertEqual(
            _parse_batch_timestamp("dance_1783980981_fea62bf2_0.mp4"),
            "dance_1783980981_fea62bf2_*.mp4",
        )

    def test_expand_digit_batch_paths_finds_siblings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            names = [
                "dance_100_deadbeef_0.mp4",
                "dance_100_deadbeef_1.mp4",
                "dance_100_deadbeef_2.mp4",
                "dance_100_deadbeef_3.mp4",
            ]
            paths = []
            for name in names:
                path = os.path.join(temp_dir, name)
                with open(path, "wb") as handle:
                    handle.write(b"x")
                paths.append(path)

            expanded = _expand_digit_batch_paths(paths[0])
            self.assertEqual(len(expanded), 4)
            self.assertEqual(set(expanded), set(paths))

    def test_non_batch_path_returns_single(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "random.mp4")
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.assertEqual(_expand_digit_batch_paths(path), [path])


if __name__ == "__main__":
    unittest.main()
