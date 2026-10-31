import { Bloom, EffectComposer, SMAA, Vignette } from '@react-three/postprocessing';

export function Effects({intensity = 0.85, threshold = 0.62}: {intensity?: number; threshold?: number}) {
  return <EffectComposer multisampling={0} enableNormalPass={false}>
    <Bloom mipmapBlur intensity={intensity} luminanceThreshold={threshold} luminanceSmoothing={0.25} radius={0.7}/>
    <Vignette offset={0.28} darkness={0.62}/>
    <SMAA/>
  </EffectComposer>;
}
