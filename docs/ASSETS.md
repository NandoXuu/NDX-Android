# Android assets and resources (section 24)

All images are produced by `tools/generate_assets.py` (Pillow). **Each density is rendered at its own pixel size**
(4x supersampled then downscaled), never copied from a single bitmap. Re-run the script after changing the design.

| Resource | Where | Notes |
|---|---|---|
| Launcher icon (legacy, square) | `assets/android/res/mipmap-{mdpi,hdpi,xhdpi,xxhdpi,xxxhdpi}/icon.png` | 48/72/96/144/192 px |
| Launcher icon (round) | `.../mipmap-*/icon_round.png` | same sizes |
| Adaptive foreground | `assets/icon/icon_foreground.png` (432 px) + `mipmap-*/ndx_ic_foreground.png` (108dp per density) | content kept inside the 66dp safe zone |
| Adaptive background | `assets/icon/icon_background.png`; color `@color/ndx_background` | wired through `icon.adaptive_*` in buildozer.spec |
| Monochrome layer | `assets/icon/icon_monochrome.png`, `mipmap-*/ndx_ic_monochrome.png`, `mipmap-anydpi-v26/ndx_launcher.xml` | see "monochrome" below |
| Notification icon | `drawable-*/ic_stat_ndx.png` | white on transparent, 24dp |
| Splash | `assets/splash/presplash.png` (1024) , `drawable-nodpi/ndx_splash.png` | `presplash.color = #0D1117` |
| Illustrations | `drawable-nodpi/ndx_empty_*.png`, `assets/images/*` | used by the Home screen |
| Vector logo | `drawable/ndx_logo_vector.xml` | resolution independent |
| Theme values | `values/ndx_colors.xml`, `values-night/ndx_colors.xml`, `values/ndx_dimens.xml` | light/dark variants |
| Fonts | `assets/fonts/DejaVuSansMono(.Bold).ttf`, `DejaVuSans(.Bold).ttf` | terminal/editor/wordmark; license file included |
| Module/navigation icons | Material Design Icons font **bundled with KivyMD** (`MDIcon`, `MDIconButton`) | native MD icons, vector, any density |

Assets are separate from user data: they live in the app package, user projects/plugins live in app-private `ndx_home/`.

## Monochrome (themed) icon - honest status
Android 13 themed icons need `<monochrome>` in the adaptive icon referenced by the manifest. python-for-android writes
its own adaptive-icon XML (without monochrome), so NDX ships the monochrome layers and `ndx_launcher.xml` but the
manifest does not reference them. To enable: point `android:icon` at `@mipmap-anydpi-v26/ndx_launcher` using a
custom manifest template (`android.manifest` template override) - not done because it could not be tested.

## Validation checklist (needs a device/emulator - not performed here)
1. Launcher icon visible & not clipped on circle / squircle / teardrop masks (Pixel launcher, Samsung One UI).
2. Splash appears with correct background on API 24, 29, 33.
3. Light and dark system themes; font scaling 0.85-1.3.
4. Screen densities: mdpi tablet, xhdpi phone, xxxhdpi phone; sizes: 360dp phone, 600dp+ tablet, landscape.
5. `python tools/verify_apk_resources.py bin/*.apk` reports no MISSING entries (this *is* checkable without a device).
