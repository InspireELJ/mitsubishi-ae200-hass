# Neutral HVAC icon sources

Original geometric artwork created for this integration on 20 October 2026.
The wall-mounted indoor unit and airflow trails contain no lettering, company
logo, Home Assistant mark, stock image, external font or network resource.
The SVG, renderer and derived PNGs are covered by the adjacent MIT LICENSE.

icon.svg is the editable source. render-icons.cjs can rebuild the PNGs when
its locally installed Sharp dependency is available; it is not a runtime
dependency. No preview or QA artefact belongs in the integration bundle.

From the repository root, run the renderer with Node.js. It writes icon.png and
icon@2x.png into assets/branding and prints their SHA-256 hashes. Copy both PNGs
into the integration's brand directory, then check the copied files:

```powershell
node assets/branding/render-icons.cjs
Copy-Item assets/branding/icon.png custom_components/ae200/brand/icon.png
Copy-Item assets/branding/icon@2x.png custom_components/ae200/brand/icon@2x.png
Get-FileHash custom_components/ae200/brand/icon.png -Algorithm SHA256
Get-FileHash custom_components/ae200/brand/icon@2x.png -Algorithm SHA256
```

Compare both hashes with the approved values below before packaging. If a
different renderer version changes the bytes, review the new outputs and update
the approved hashes together; do not assume that a successful render preserves
the approved bytes.

The approved integration outputs are:

- custom_components/ae200/brand/icon.png:
  936467e16259d388c109e9971e5e4d83a68912943d90e4a7de5b1b86e689672b
- custom_components/ae200/brand/icon@2x.png:
  4a4f3934b23012916afe5a5da117893a143fce02df926e278b79c99ac1702ac3

Home Assistant supports local custom-integration brand images from 2026.3.
The minimum supported Core version remains 2025.12.0; that older version uses
the brands CDN. Static images do not change the integration's runtime API.
See the official brand-image documentation:
https://contributors.home-assistant.io/core/integrations/brands/#brand-images-for-custom-integrations

This source inclusion is an unreleased offline candidate. It does not establish
that the images have been installed or displayed by a live Home Assistant UI.
