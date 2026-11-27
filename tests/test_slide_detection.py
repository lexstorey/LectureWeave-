import unittest

import numpy as np

from slide_detection import (
    DetectionConfig,
    block_change_ratio,
    build_activity_mask,
    choose_change_threshold,
    hash_distance,
    perceptual_hash,
)


class SlideDetectionTests(unittest.TestCase):
    def setUp(self):
        self.config = DetectionConfig(analysis_width=64, analysis_height=48, block_size=8)

    def test_identical_frames_do_not_change(self):
        frame = np.full((48, 64, 3), 100, dtype=np.uint8)
        self.assertEqual(0.0, block_change_ratio(frame, frame.copy(), self.config))

    def test_meaningful_blocks_are_detected(self):
        left = np.zeros((48, 64, 3), dtype=np.uint8)
        right = left.copy()
        right[:24, :32] = 255
        self.assertGreater(block_change_ratio(left, right, self.config), 0.20)

    def test_persistent_moving_region_is_masked(self):
        frames = []
        for index in range(8):
            frame = np.zeros((48, 64, 3), dtype=np.uint8)
            frame[:16, :16] = 255 if index % 2 else 0
            frames.append(frame)
        mask = build_activity_mask(frames, self.config)
        self.assertIsNotNone(mask)
        self.assertTrue(mask[0, 0])

    def test_excessive_activity_mask_is_rejected(self):
        frames = [
            np.full((48, 64, 3), 255 if index % 2 else 0, dtype=np.uint8)
            for index in range(8)
        ]
        self.assertIsNone(build_activity_mask(frames, self.config))

    def test_bimodal_footage_gets_calibrated_threshold(self):
        threshold, mode = choose_change_threshold(
            [0.0, 0.001, 0.002, 0.001, 0.08, 0.09, 0.001, 0.0], self.config
        )
        self.assertIn(mode, {"bimodal", "adaptive"})
        self.assertGreater(threshold, 0.01)
        self.assertLess(threshold, 0.08)

    def test_perceptual_hash_separates_different_slides(self):
        left = np.zeros((48, 64, 3), dtype=np.uint8)
        left[5:20, 5:25] = 255
        right = np.zeros((48, 64, 3), dtype=np.uint8)
        right[25:45, 35:60] = 255
        self.assertGreater(hash_distance(perceptual_hash(left), perceptual_hash(right)), 5)


if __name__ == "__main__":
    unittest.main()
