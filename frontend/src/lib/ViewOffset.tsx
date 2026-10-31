import { useEffect } from 'react';
import { useThree } from '@react-three/fiber';

export type Insets = {left: number; right: number; top: number; bottom: number};

/** Shift the projection centre into the part of the canvas not covered by HUD panels. */
export function ViewOffset({left, right, top, bottom}: Insets) {
  const camera = useThree(s => s.camera);
  const size = useThree(s => s.size);
  useEffect(() => {
    const cx = left + Math.max(0, size.width - left - right) / 2;
    const cy = top + Math.max(0, size.height - top - bottom) / 2;
    camera.setViewOffset(size.width, size.height, size.width / 2 - cx, size.height / 2 - cy, size.width, size.height);
    camera.updateProjectionMatrix();
    return () => { camera.clearViewOffset(); camera.updateProjectionMatrix(); };
  }, [camera, size.width, size.height, left, right, top, bottom]);
  return null;
}
