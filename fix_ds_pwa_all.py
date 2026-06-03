from pathlib import Path
import re
import shutil

BASE_DIR = Path(__file__).resolve().parent

TEMPLATE_DIR = BASE_DIR / "templates" / "customerportal"
STATIC_IMG_DIR = BASE_DIR / "static" / "img"
MANIFEST_DIR = STATIC_IMG_DIR / "manifest"

FAVICON = STATIC_IMG_DIR / "favicon.png"
APPLE_ICON = STATIC_IMG_DIR / "ds-express-icon-v5.png"
APPLE_TOUCH = STATIC_IMG_DIR / "apple-touch-icon.png"
MANIFEST_FILE = MANIFEST_DIR / "ds-express.webmanifest"

VERSION = "20260604ds5"


PWA_HEAD = f"""{{% load static %}}
{{% static 'img/ds-express-icon-v5.png' as ds_app_icon %}}
{{% static 'img/manifest/ds-express.webmanifest' as ds_manifest %}}

<title>DS EXPRESS</title>

<link rel="icon" type="image/png" href="{{{{ ds_app_icon }}}}?v={VERSION}">
<link rel="shortcut icon" type="image/png" href="{{{{ ds_app_icon }}}}?v={VERSION}">

<link rel="apple-touch-icon" sizes="180x180" href="{{{{ ds_app_icon }}}}?v={VERSION}">
<link rel="apple-touch-icon-precomposed" sizes="180x180" href="{{{{ ds_app_icon }}}}?v={VERSION}">

<link rel="manifest" href="{{{{ ds_manifest }}}}?v={VERSION}">

<meta name="application-name" content="DS EXPRESS">
<meta name="apple-mobile-web-app-title" content="DS EXPRESS">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="theme-color" content="#0f7a45">
<meta name="description" content="DS EXPRESS Seller Portal">

<script>
  document.title = "DS EXPRESS";
</script>
"""


def copy_icons():
    if not FAVICON.exists():
        raise FileNotFoundError(f"Cannot find {FAVICON}")

    shutil.copyfile(FAVICON, APPLE_ICON)
    shutil.copyfile(FAVICON, APPLE_TOUCH)

    print("Copied icon:")
    print("-", APPLE_ICON.relative_to(BASE_DIR))
    print("-", APPLE_TOUCH.relative_to(BASE_DIR))


def write_manifest():
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)

    manifest = f"""{{
  "name": "DS EXPRESS",
  "short_name": "DS EXPRESS",
  "description": "DS EXPRESS Seller Portal",
  "start_url": "/portal/",
  "scope": "/portal/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#0f7a45",
  "icons": [
    {{
      "src": "/static/img/ds-express-icon-v5.png?v={VERSION}",
      "sizes": "180x180",
      "type": "image/png",
      "purpose": "any"
    }},
    {{
      "src": "/static/img/ds-express-icon-v5.png?v={VERSION}",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    }},
    {{
      "src": "/static/img/ds-express-icon-v5.png?v={VERSION}",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    }}
  ]
}}
"""
    MANIFEST_FILE.write_text(manifest, encoding="utf-8")
    print("Updated manifest:")
    print("-", MANIFEST_FILE.relative_to(BASE_DIR))


def clean_old_head_bits(text):
    # Remove old title tags
    text = re.sub(
        r"<title>.*?</title>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove old Django title blocks
    text = re.sub(
        r"{%\s*block\s+title\s*%}.*?{%\s*endblock\s*%}",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove old icon / manifest / app meta lines
    patterns = [
        r'\s*<link[^>]+rel=["\']icon["\'][^>]*>\s*',
        r'\s*<link[^>]+rel=["\']shortcut icon["\'][^>]*>\s*',
        r'\s*<link[^>]+rel=["\']apple-touch-icon["\'][^>]*>\s*',
        r'\s*<link[^>]+rel=["\']apple-touch-icon-precomposed["\'][^>]*>\s*',
        r'\s*<link[^>]+rel=["\']manifest["\'][^>]*>\s*',
        r'\s*<meta[^>]+name=["\']application-name["\'][^>]*>\s*',
        r'\s*<meta[^>]+name=["\']apple-mobile-web-app-title["\'][^>]*>\s*',
        r'\s*<meta[^>]+name=["\']apple-mobile-web-app-capable["\'][^>]*>\s*',
        r'\s*<meta[^>]+name=["\']mobile-web-app-capable["\'][^>]*>\s*',
        r'\s*<meta[^>]+name=["\']apple-mobile-web-app-status-bar-style["\'][^>]*>\s*',
        r'\s*<meta[^>]+name=["\']theme-color["\'][^>]*>\s*',
        r'\s*<meta[^>]+name=["\']description["\'][^>]*>\s*',
        r'\s*<script>\s*document\.title\s*=\s*["\']DS EXPRESS["\'];\s*</script>\s*',
    ]

    for pattern in patterns:
        text = re.sub(pattern, "\n", text, flags=re.IGNORECASE | re.DOTALL)

    # Remove old static aliases for favicon/manifest if present
    text = re.sub(
        r"\s*{%\s*static\s+['\"]img/favicon\.png['\"]\s+as\s+ds_favicon\s*%}\s*",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*{%\s*static\s+['\"]img/manifest/ds-express\.webmanifest['\"]\s+as\s+ds_manifest\s*%}\s*",
        "\n",
        text,
        flags=re.IGNORECASE,
    )

    return text


def insert_pwa_head(text):
    text = clean_old_head_bits(text)

    # If file has <head>, insert PWA block after viewport meta if possible, otherwise after <head>
    viewport_re = re.compile(
        r'(<meta\s+name=["\']viewport["\'][^>]*>)',
        flags=re.IGNORECASE | re.DOTALL,
    )

    if viewport_re.search(text):
        text = viewport_re.sub(rf"\1\n\n{PWA_HEAD}", text, count=1)
        return text

    head_re = re.compile(r"(<head[^>]*>)", flags=re.IGNORECASE)
    if head_re.search(text):
        text = head_re.sub(rf"\1\n\n{PWA_HEAD}", text, count=1)
        return text

    return text


def fix_template_titles_only(text):
    # For templates that extend base and do not have <head>, just force block title.
    text = re.sub(
        r"{%\s*block\s+title\s*%}.*?{%\s*endblock\s*%}",
        "{% block title %}DS EXPRESS{% endblock %}",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # If file extends base but has no title block, add it after load static or extends line
    if "{% extends" in text and "{% block title %}" not in text:
        lines = text.splitlines()
        insert_at = 1
        for i, line in enumerate(lines[:5]):
          if "{% load static %}" in line:
              insert_at = i + 1
        lines.insert(insert_at, "{% block title %}DS EXPRESS{% endblock %}")
        text = "\n".join(lines) + "\n"

    return text


def patch_templates():
    changed = []

    for path in TEMPLATE_DIR.rglob("*.html"):
        old = path.read_text(encoding="utf-8")
        new = old

        if "<head" in new.lower():
            new = insert_pwa_head(new)
        else:
            new = fix_template_titles_only(new)

        # Replace visible app title text only where it is app name
        # Do not remove labels like Seller Login inside the form.
        new = new.replace("DS Express</div>", "DS EXPRESS</div>")
        new = new.replace(">DS Express<", ">DS EXPRESS<")

        if new != old:
            path.write_text(new, encoding="utf-8")
            changed.append(path.relative_to(BASE_DIR))

    print("Updated templates:", len(changed))
    for item in changed:
        print("-", item)


def main():
    copy_icons()
    write_manifest()
    patch_templates()

    print("")
    print("DONE. Now run:")
    print("git status")
    print("git add .")
    print('git commit -m "Force DS EXPRESS PWA name and icon everywhere"')
    print("git push origin main")


if __name__ == "__main__":
    main()