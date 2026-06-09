# Color Palette — "Cyberpunk" Theme

The frontend uses a Cyberpunk-themed palette built on Tailwind + CSS variables.
Colors are stored in [`src/app/globals.css`](src/app/globals.css) as HSL triplets
(`H S% L%`) and consumed via `hsl(var(--token))` in
[`tailwind.config.ts`](tailwind.config.ts).

- **Source of truth:** the HSL triplets in `globals.css`. Hex values below are
  computed conversions for reference.
- Dark mode activates via the `.dark` class (`darkMode: "class"`), toggled in
  [`src/components/theme-toggle.tsx`](src/components/theme-toggle.tsx).
- `success` and `warning` are custom tokens beyond the standard shadcn/ui set.

## ☀️ Light theme — pale lavender surfaces, deep violet text, teal-cyan primary

| Token                  | HSL           | Hex       | Note            |
| ---------------------- | ------------- | --------- | --------------- |
| `background`           | `260 50% 98%` | `#F9F7FC` | pale lavender   |
| `foreground`           | `263 70% 8%`  | `#110623` | deep violet text |
| `card`                 | `260 40% 100%`| `#FFFFFF` |                 |
| `card-foreground`      | `263 70% 8%`  | `#110623` |                 |
| `popover`              | `0 0% 100%`   | `#FFFFFF` |                 |
| `popover-foreground`   | `263 70% 8%`  | `#110623` |                 |
| `primary`              | `176 100% 30%`| `#00998F` | teal-cyan       |
| `primary-foreground`   | `0 0% 100%`   | `#FFFFFF` |                 |
| `secondary`            | `263 45% 94%` | `#EEE9F7` |                 |
| `secondary-foreground` | `263 60% 12%` | `#1A0C31` |                 |
| `muted`                | `263 25% 93%` | `#ECE9F2` |                 |
| `muted-foreground`     | `262 12% 46%` | `#726783` |                 |
| `accent`               | `263 45% 94%` | `#EEE9F7` |                 |
| `accent-foreground`    | `263 60% 12%` | `#1A0C31` |                 |
| `destructive`          | `345 82% 48%` | `#DF1648` | magenta-red     |
| `success`              | `158 84% 30%` | `#0C8D5E` | green           |
| `warning`              | `45 100% 42%` | `#D6A100` | amber           |
| `border`               | `262 30% 88%` | `#DED7EA` |                 |
| `input`                | `262 30% 88%` | `#DED7EA` |                 |
| `ring`                 | `176 100% 30%`| `#00998F` | = primary       |

`--radius: 0.5rem`

## 🌙 Dark theme — violet-black surfaces, neon cyan primary, hot magenta accents

| Token                  | HSL            | Hex       | Note             |
| ---------------------- | -------------- | --------- | ---------------- |
| `background`           | `264 90% 4%`   | `#080113` | violet-black     |
| `foreground`           | `176 20% 90%`  | `#E0EBEA` | pale cyan-white  |
| `card`                 | `262 60% 7%`   | `#0F071D` |                  |
| `card-foreground`      | `176 20% 90%`  | `#E0EBEA` |                  |
| `popover`              | `262 60% 7%`   | `#0F071D` |                  |
| `popover-foreground`   | `176 20% 90%`  | `#E0EBEA` |                  |
| `primary`              | `176 100% 50%` | `#00FFEE` | neon cyan        |
| `primary-foreground`   | `264 90% 4%`   | `#080113` |                  |
| `secondary`            | `263 45% 12%`  | `#1B112C` |                  |
| `secondary-foreground` | `176 20% 90%`  | `#E0EBEA` |                  |
| `muted`                | `263 35% 13%`  | `#1E162D` |                  |
| `muted-foreground`     | `262 14% 55%`  | `#887C9C` |                  |
| `accent`               | `263 45% 12%`  | `#1B112C` |                  |
| `accent-foreground`    | `176 20% 90%`  | `#E0EBEA` |                  |
| `destructive`          | `346 100% 58%` | `#FF295B` | hot magenta      |
| `success`              | `151 100% 44%` | `#00E074` | neon green       |
| `warning`              | `50 100% 50%`  | `#FFD400` | electric yellow  |
| `border`               | `262 40% 15%`  | `#221736` |                  |
| `input`                | `260 40% 17%`  | `#261A3D` |                  |
| `ring`                 | `176 100% 50%` | `#00FFEE` | = primary        |
