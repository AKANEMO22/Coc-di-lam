// Geometry of public/images/interview-background.png, in original image pixels.
// Trace the rear edge of the desktop rather than clipping at a viewport percentage.
const IMAGE_WIDTH = 1672;
const IMAGE_HEIGHT = 941;
const TABLE_EDGE: ReadonlyArray<readonly [number, number]> = [
  [0, 728], [320, 728], [640, 729], [960, 732], [1280, 734], [1672, 736],
];

export function interviewSceneLayout(width: number, height: number) {
  const scale = Math.max(width / IMAGE_WIDTH, height / IMAGE_HEIGHT);
  const imageWidth = IMAGE_WIDTH * scale;
  const imageHeight = IMAGE_HEIGHT * scale;
  const x = (width - imageWidth) / 2;
  const y = (height - imageHeight) * 0.54;
  const foreground = [
    ...TABLE_EDGE,
    [IMAGE_WIDTH, IMAGE_HEIGHT] as const,
    [0, IMAGE_HEIGHT] as const,
  ].map(([px, py]) => [x + px * scale, y + py * scale] as const);
  // Interpolate the traced edge at the image's horizontal centre.
  const centreY = 729 + (IMAGE_WIDTH / 2 - 640) / (960 - 640) * (732 - 729);
  return {
    x, y, imageWidth, imageHeight, foreground,
    deskY: y + centreY * scale,
    clipPath: `polygon(${foreground.map(([px, py]) => `${px}px ${py}px`).join(', ')})`,
  };
}
