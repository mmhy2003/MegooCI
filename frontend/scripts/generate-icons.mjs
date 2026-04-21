import sharp from "sharp";
import { mkdirSync, copyFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = join(__dirname, "..", "public", "icons", "icon-512.png");
const out = join(__dirname, "..", "public", "icons");

mkdirSync(out, { recursive: true });

const sizes = [16, 32, 48, 72, 96, 128, 144, 152, 180, 192, 384];

async function generate() {
  for (const s of sizes) {
    await sharp(src).resize(s, s).png().toFile(join(out, `icon-${s}.png`));
    console.log(`  icon-${s}.png`);
  }

  // Maskable icons (same image, used with safe-zone padding by the OS)
  for (const s of [192, 512]) {
    await sharp(src).resize(s, s).png().toFile(join(out, `icon-maskable-${s}.png`));
    console.log(`  icon-maskable-${s}.png`);
  }

  // Apple touch icon (180x180)
  copyFileSync(join(out, "icon-180.png"), join(out, "apple-touch-icon.png"));
  console.log("  apple-touch-icon.png");

  // Favicon aliases
  copyFileSync(join(out, "icon-16.png"), join(out, "favicon-16.png"));
  copyFileSync(join(out, "icon-32.png"), join(out, "favicon-32.png"));
  copyFileSync(join(out, "icon-48.png"), join(out, "favicon-48.png"));
  console.log("  favicon-16.png, favicon-32.png, favicon-48.png");

  // Generate favicon.ico (32x32 PNG works as .ico in all modern browsers)
  await sharp(src).resize(32, 32).png().toFile(join(out, "favicon.ico"));
  console.log("  favicon.ico");

  console.log("\nAll icons generated.");
}

generate().catch((e) => {
  console.error(e);
  process.exit(1);
});
