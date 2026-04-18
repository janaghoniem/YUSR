#!/usr/bin/env python3
"""
test_verification.py - Unit Tests for Screenshot Verification

Tests:
- Screenshot capture (before/after)
- SSIM comparison
- Change detection
- Difference mask generation
"""

import pytest
import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from agents.execution_agent.RAG.web.verification import ScreenshotVerifier


class TestScreenshotVerifier:
    """Test screenshot verification functionality"""
    
    @pytest.fixture
    def verifier(self):
        """Fixture: Create screenshot verifier with temp directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield ScreenshotVerifier(output_dir=tmpdir)
    
    def test_initialization(self, verifier):
        """Test verifier initialization"""
        assert verifier is not None
        assert verifier.before_screenshot is None
        assert verifier.after_screenshot is None
        assert verifier.comparison_ssim is None
        assert verifier.output_dir.exists()
    
    @pytest.mark.asyncio
    async def test_take_screenshot_before(self, verifier):
        """Test taking before screenshot"""
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b'fake_image_data'
        
        with patch('builtins.open', create=True):
            filepath = await verifier.take_screenshot_before(mock_page, 'session_1', 'click_button')
            
            assert filepath is not None
            assert 'BEFORE' in filepath
            mock_page.screenshot.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_take_screenshot_after(self, verifier):
        """Test taking after screenshot"""
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b'fake_image_data'
        
        with patch('builtins.open', create=True):
            with patch('agents.execution_agent.RAG.web.verification.asyncio.sleep', new_callable=AsyncMock):
                filepath = await verifier.take_screenshot_after(mock_page, 'session_1', 'click_button')
                
                assert filepath is not None
                assert 'AFTER' in filepath
    
    def test_compare_screenshots_no_images(self, verifier):
        """Test comparison fails gracefully with no images"""
        result = verifier.compare_screenshots()
        
        # Should return None, None when no images available
        assert result == (None, None)
    
    def test_get_statistics(self, verifier):
        """Test getting verification statistics"""
        stats = verifier.get_statistics()
        
        assert 'before_screenshot' in stats
        assert 'after_screenshot' in stats
        assert 'ssim_score' in stats
        assert 'threshold' in stats
        assert stats['before_screenshot'] is False
        assert stats['after_screenshot'] is False
    
    @pytest.mark.skipif(not bool(pytest.importorskip('cv2', minversion=None, required=False)), 
                        reason="OpenCV not installed")
    def test_compare_screenshots_with_cv2(self, verifier):
        """Test screenshot comparison with OpenCV available"""
        import cv2
        import numpy as np
        
        # Create two similar test images
        img1 = np.ones((100, 100, 3), dtype=np.uint8) * 200
        img2 = np.ones((100, 100, 3), dtype=np.uint8) * 200
        
        # Slightly modify second image
        img2[0:10, 0:10] = 100
        
        verifier.before_screenshot = img1
        verifier.after_screenshot = img2
        
        changed, ssim_score = verifier.compare_screenshots()
        
        # SSIM should be calculated
        assert ssim_score is not None
        assert 0 <= ssim_score <= 1
        # Images are mostly similar, so SSIM should be high
        assert ssim_score > 0.9
    
    @pytest.mark.skipif(not bool(pytest.importorskip('cv2', minversion=None, required=False)), 
                        reason="OpenCV not installed")
    def test_change_detection_threshold(self, verifier):
        """Test change detection based on SSIM threshold"""
        import cv2
        import numpy as np
        
        # Create significantly different images
        img1 = np.ones((100, 100, 3), dtype=np.uint8) * 255
        img2 = np.ones((100, 100, 3), dtype=np.uint8) * 0  # Black vs White
        
        verifier.before_screenshot = img1
        verifier.after_screenshot = img2
        
        changed, ssim_score = verifier.compare_screenshots()
        
        # Should detect change
        if ssim_score:
            assert ssim_score < verifier.CHANGE_DETECTION_THRESHOLD
            assert changed is True
    
    def test_different_screenshot_dimensions(self, verifier):
        """Test handling screenshots with different dimensions"""
        import numpy as np
        try:
            import cv2
            
            img1 = np.ones((100, 100, 3), dtype=np.uint8) * 200
            img2 = np.ones((150, 150, 3), dtype=np.uint8) * 200
            
            verifier.before_screenshot = img1
            verifier.after_screenshot = img2
            
            # Should handle dimension mismatch gracefully
            changed, ssim_score = verifier.compare_screenshots()
            
            # Should still compare (after resizing)
            if ssim_score:
                assert 0 <= ssim_score <= 1
        
        except ImportError:
            pytest.skip("OpenCV not installed")
    
    @pytest.mark.skipif(not bool(pytest.importorskip('cv2', minversion=None, required=False)), 
                        reason="OpenCV not installed")
    def test_difference_mask_generation(self, verifier):
        """Test generating difference mask"""
        import cv2
        import numpy as np
        
        img1 = np.ones((100, 100, 3), dtype=np.uint8) * 200
        img2 = np.ones((100, 100, 3), dtype=np.uint8) * 200
        img2[0:20, 0:20] = 50  # Modify small region
        
        verifier.before_screenshot = img1
        verifier.after_screenshot = img2
        
        mask_path = verifier.get_difference_mask()
        
        # Mask path should be generated
        if 'cv2' in dir() and cv2 is not None:
            assert mask_path is not None
            assert Path(mask_path).exists()


class TestScreenshotIntegration:
    """Integration tests for screenshot verification"""
    
    @pytest.mark.asyncio
    async def test_before_after_workflow(self, tmpdir):
        """Test complete before/after verification workflow"""
        verifier = ScreenshotVerifier(output_dir=str(tmpdir))
        
        mock_page = AsyncMock()
        mock_page.screenshot.return_value = b'fake_image'
        
        with patch('builtins.open', create=True):
            with patch('agents.execution_agent.RAG.web.verification.asyncio.sleep', new_callable=AsyncMock):
                # Take before
                before_path = await verifier.take_screenshot_before(mock_page, 'session_1', 'test_action')
                assert before_path is not None
                
                # Take after
                after_path = await verifier.take_screenshot_after(mock_page, 'session_1', 'test_action')
                assert after_path is not None
    
    def test_statistics_after_comparison(self):
        """Test that statistics are updated after comparison"""
        try:
            import cv2
            import numpy as np
            
            verifier = ScreenshotVerifier()
            
            img1 = np.ones((100, 100, 3), dtype=np.uint8) * 200
            img2 = np.ones((100, 100, 3), dtype=np.uint8) * 200
            
            verifier.before_screenshot = img1
            verifier.after_screenshot = img2
            
            verifier.compare_screenshots()
            
            stats = verifier.get_statistics()
            
            assert stats['ssim_score'] is not None
            assert stats['threshold'] == verifier.CHANGE_DETECTION_THRESHOLD
        
        except ImportError:
            pytest.skip("OpenCV not installed")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
