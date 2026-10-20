# Repository images

Images used by the top-level README and the GitHub repository settings. Every
dashboard image here must be a real capture of the self-hosted dashboard. Do
not use mock data (`mg_mock=1`), mockups, or edited pixels.

| File | Used by | Size | Status |
|---|---|---|---|
| `dashboard-overview.png` | README hero image | 1280x1215 | Captured from seeded OSS server |
| `social-preview.svg` | Source for the social preview | 1280x640 | Present |
| `social-preview.png` | GitHub social preview | 1280x640, under 1 MB | Present |

## Capture `dashboard-overview.png`

1. Start a clean server: `MG_TOKENS=dev-token docker compose up`.
2. Seed demo traffic through the published SDK:

   ```bash
   python -m pip install 'metergraph>=0.4,<1'
   MG_TOKEN=dev-token python scripts/seed_demo.py 3
   ```

3. Open http://localhost:8787, enter `dev-token`, and stay on **Overview** with
   the **7d** range and all environments selected.
4. Set the browser viewport to 1280x800 and capture the full dashboard page. Do
   not include browser chrome, bookmarks, or other tabs.
5. Save as `docs/images/dashboard-overview.png`.

The seeded data is synthetic (`demo` environment, example function and route
names), so the capture contains no customer data. Check the image anyway before
committing it.

## Render `social-preview.png`

Render `social-preview.svg` at exactly 1280x640, for example with a headless
browser screenshot, and confirm the fonts look right. Upload the PNG in the
repository's **Settings > General > Social preview**. The SVG is the reviewed,
editable source.
