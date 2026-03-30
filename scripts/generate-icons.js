const sharp = require('sharp');
const path = require('path');

// User's exact SVG path (viewBox 0 0 640 640)
const SYNC_PATH = 'M129.9 292.5C143.2 199.5 223.3 128 320 128C373 128 421 149.5 455.8 184.2C456 184.4 456.2 184.6 456.4 184.8L464 192L416.1 192C398.4 192 384.1 206.3 384.1 224C384.1 241.7 398.4 256 416.1 256L544.1 256C561.8 256 576.1 241.7 576.1 224L576.1 96C576.1 78.3 561.8 64 544.1 64C526.4 64 512.1 78.3 512.1 96L512.1 149.4L500.8 138.7C454.5 92.6 390.5 64 320 64C191 64 84.3 159.4 66.6 283.5C64.1 301 76.2 317.2 93.7 319.7C111.2 322.2 127.4 310 129.9 292.6zM573.4 356.5C575.9 339 563.7 322.8 546.3 320.3C528.9 317.8 512.6 330 510.1 347.4C496.8 440.4 416.7 511.9 320 511.9C267 511.9 219 490.4 184.2 455.7C184 455.5 183.8 455.3 183.6 455.1L176 447.9L223.9 447.9C241.6 447.9 255.9 433.6 255.9 415.9C255.9 398.2 241.6 383.9 223.9 383.9L96 384C87.5 384 79.3 387.4 73.3 393.5C67.3 399.6 63.9 407.7 64 416.3L65 543.3C65.1 561 79.6 575.2 97.3 575C115 574.8 129.2 560.4 129 542.7L128.6 491.2L139.3 501.3C185.6 547.4 249.5 576 320 576C449 576 555.7 480.6 573.4 356.5z';
const VIEWBOX = 640;

const BG_COLOR = '#000000';
const ICON_COLOR = '#ffffff';

function makeSVG(width, height, iconScale, iconOffsetX, iconOffsetY, bgColor) {
  const bg = bgColor ? `<rect width="${width}" height="${height}" fill="${bgColor}"/>` : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
    ${bg}
    <g transform="translate(${iconOffsetX}, ${iconOffsetY}) scale(${iconScale})">
      <path fill="${ICON_COLOR}" d="${SYNC_PATH}"/>
    </g>
  </svg>`;
}

async function main() {
  const assetsDir = path.join(__dirname, '..', 'assets');

  // 1. icon.png - 1024x1024, solid dark background, icon at 65%
  const iconScale = (1024 * 0.65) / VIEWBOX;
  const iconOffset = (1024 - VIEWBOX * iconScale) / 2;
  const iconSVG = makeSVG(1024, 1024, iconScale, iconOffset, iconOffset, BG_COLOR);
  await sharp(Buffer.from(iconSVG)).png().toFile(path.join(assetsDir, 'icon.png'));
  console.log('Generated icon.png (1024x1024)');

  // 2. adaptive-icon.png - 1024x1024, transparent bg, icon centered in safe zone (~50%)
  const adaptiveScale = (1024 * 0.50) / VIEWBOX;
  const adaptiveOffset = (1024 - VIEWBOX * adaptiveScale) / 2;
  const adaptiveSVG = makeSVG(1024, 1024, adaptiveScale, adaptiveOffset, adaptiveOffset, null);
  await sharp(Buffer.from(adaptiveSVG)).png().toFile(path.join(assetsDir, 'adaptive-icon.png'));
  console.log('Generated adaptive-icon.png (1024x1024)');

  // 3. splash-icon.png - 512x512, transparent bg, icon at 70%
  const splashScale = (512 * 0.7) / VIEWBOX;
  const splashOffset = (512 - VIEWBOX * splashScale) / 2;
  const splashSVG = makeSVG(512, 512, splashScale, splashOffset, splashOffset, null);
  await sharp(Buffer.from(splashSVG)).png().toFile(path.join(assetsDir, 'splash-icon.png'));
  console.log('Generated splash-icon.png (512x512)');

  // 4. favicon.png - 48x48, solid dark background
  const favScale = (48 * 0.7) / VIEWBOX;
  const favOffset = (48 - VIEWBOX * favScale) / 2;
  const favSVG = makeSVG(48, 48, favScale, favOffset, favOffset, BG_COLOR);
  await sharp(Buffer.from(favSVG)).png().toFile(path.join(assetsDir, 'favicon.png'));
  console.log('Generated favicon.png (48x48)');

  console.log('\nAll icons generated successfully!');
}

main().catch(console.error);
