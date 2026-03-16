#!/usr/bin/env python3

import argparse
import base64
import html
import io
import json
import os
import re
import struct
import subprocess
import sys

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, *args, **kwargs):
        return iterable


template = ''
inkscape_path = inkscape_path = r'C:\Program Files\Inkscape\bin\inkscape.exe'
replace_text = 'Име Фамилия'
subtitle_replace_text = 'описание'
overwrite = False

# Logo box definitions: rect_id -> (x, y, width, height, flipped)
# flipped=True  → logo is rotated 180° (both axes) to match the mirrored top half
# flipped=False → logo is placed normally
_LOGO_BOXES = {
    'Rectangle 341': (233, 772, 130, 70, False),  # bottom, normal
    'Rectangle 342': (233,   0, 130, 70, True),   # top,    flipped
}

_embed_logo_call_count = [0]


def ensure_tree_exists(path):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)


def escape_xml(text):
    """Escape XML special characters to make text safe for XML/SVG."""
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))


def html_entity_encode(text):
    """Encode text to HTML entities (hex format like &#x418;)."""
    result = []
    for c in text:
        if ord(c) > 127:
            result.append(f'&#x{ord(c):x};')
        else:
            result.append(c)
    return ''.join(result)


def replace_text_with_html_support(template, old_text, new_text):
    """
    Replace text in template, handling both plain text and HTML entity-encoded versions.
    Escapes XML special characters in the replacement text to ensure valid XML/SVG.
    """
    # Escape XML special characters in the new text
    escaped_new_text = escape_xml(new_text)

    # Try plain text replacement first
    if old_text in template:
        return template.replace(old_text, escaped_new_text)

    # Try HTML entity-encoded version (hex format: &#x418;)
    encoded_old_hex = html_entity_encode(old_text)
    if encoded_old_hex in template:
        # Escape first, then encode to HTML entities
        encoded_new_hex = html_entity_encode(escaped_new_text)
        return template.replace(encoded_old_hex, encoded_new_hex)

    # Try HTML entity-encoded version (decimal format: &#1048;)
    encoded_old_decimal = ''.join(f'&#{ord(c)};' if ord(c) > 127 else c for c in old_text)
    if encoded_old_decimal in template:
        # Escape first, then encode to HTML entities
        encoded_new_decimal = ''.join(f'&#{ord(c)};' if ord(c) > 127 else c for c in escaped_new_text)
        return template.replace(encoded_old_decimal, encoded_new_decimal)

    # Try unescaping HTML entities in template and then replacing
    # This handles cases where the encoding might be mixed or in a different format
    try:
        unescaped_template = html.unescape(template)
        if old_text in unescaped_template:
            result = unescaped_template.replace(old_text, escaped_new_text)
            # Check if original template had any HTML entities for this text
            # If so, re-encode the result in hex format (most common in SVG exports)
            if encoded_old_hex in template or encoded_old_decimal in template:
                return html_entity_encode(result)
            return result
    except Exception:
        pass

    # If nothing found, return original (might cause issues, but at least won't crash)
    return template


def center_text_in_svg(svg_content, text_content):
    """
    Center text in SVG by adding text-anchor="middle" and updating x coordinate.
    Works at the <text> element level so it can account for matrix transforms
    (e.g. flipped/mirrored text) and compute the correct local center x for each
    text element independently.
    """
    # Calculate the global center x from viewBox or width
    viewbox_match = re.search(r'viewBox=["\']([^"\']+)["\']', svg_content)
    width_match = re.search(r'width=["\']([^"\']+)["\']', svg_content)

    center_x_global = None
    if viewbox_match:
        parts = viewbox_match.group(1).split()
        if len(parts) >= 3:
            center_x_global = float(parts[2]) / 2
    elif width_match:
        width_str = width_match.group(1).replace('px', '').strip()
        try:
            center_x_global = float(width_str) / 2
        except ValueError:
            pass

    if center_x_global is None:
        center_x_global = 297.5  # Default for A4 width (595/2)

    # Process each <text> … </text> element individually so we can read its
    # transform attribute before deciding what local x to use for centering.
    text_element_pattern = r'(<text\b)((?:[^>]|"[^"]*"|\'[^\']*\')*>)(.*?)(</text>)'

    def process_text_element(match):
        tag_open    = match.group(1)   # "<text"
        tag_attrs   = match.group(2)   # everything up to and including the closing ">"
        body        = match.group(3)   # content between <text> and </text>
        tag_close   = match.group(4)   # "</text>"

        # Check whether this element contains our target text (any encoding)
        try:
            unescaped_body = html.unescape(body)
            contains = (text_content in unescaped_body) or (text_content in body)
        except Exception:
            contains = text_content in body

        if not contains:
            return match.group(0)  # not our element, leave untouched

        # Determine the local center x, accounting for a matrix transform.
        # SVG matrix(a b c d e f) — when a == -1 the text is horizontally flipped
        # and the global center maps to local x = e - center_x_global.
        center_x_local = center_x_global
        full_attrs = tag_open + tag_attrs
        matrix_match = re.search(
            r'transform\s*=\s*["\']matrix\(([^)]+)\)["\']', full_attrs
        )
        if matrix_match:
            raw = matrix_match.group(1)
            # values may be comma- or whitespace-separated
            values = re.split(r'[\s,]+', raw.strip())
            if len(values) == 6:
                try:
                    a  = float(values[0])
                    tx = float(values[4])  # e (x translation)
                    if a < 0:
                        # Flipped horizontally: local_x = tx - global_x
                        center_x_local = tx - center_x_global
                except ValueError:
                    pass

        # Update every tspan x attribute inside this element
        def update_tspan_x(m):
            return m.group(1) + str(center_x_local) + m.group(3)

        body = re.sub(
            r'(<tspan\b[^>]*\bx=["\'])([^"\']+)(["\'])',
            update_tspan_x,
            body,
            flags=re.IGNORECASE,
        )

        # Add text-anchor="middle" to the <text> opening tag if not already present
        if 'text-anchor' not in full_attrs:
            tag_attrs = tag_attrs.rstrip('>').rstrip() + ' text-anchor="middle">'

        return tag_open + tag_attrs + body + tag_close

    svg_content = re.sub(
        text_element_pattern,
        process_text_element,
        svg_content,
        flags=re.DOTALL,
    )

    return svg_content


def embed_logos_in_svg(svg_content, logo_svg_content):
    """
    Inline a logo SVG into both white logo boxes in the badge template.

    Box layout (from _LOGO_BOXES):
      Rectangle 341 – bottom half, normal orientation
      Rectangle 342 – top half,    flipped 180° (matching the mirrored name text)

    The logo is scaled uniformly to fit inside the box with 10 px padding on
    every side, then centered both horizontally and vertically.

    IDs inside the logo SVG are namespaced with a unique prefix so they never
    collide with the template's own IDs across multiple generated files.
    """
    _embed_logo_call_count[0] += 1
    uid = f'lg{_embed_logo_call_count[0]:04d}'

    # ── Parse logo viewBox ──────────────────────────────────────────────────
    vb_match = re.search(r'viewBox=["\']([^"\']+)["\']', logo_svg_content)
    if vb_match:
        vb_parts = vb_match.group(1).split()
        vb_x, vb_y, vb_w, vb_h = (float(v) for v in vb_parts[:4])
    else:
        w_m = re.search(r'<svg\b[^>]+width=["\']([0-9.]+)', logo_svg_content)
        h_m = re.search(r'<svg\b[^>]+height=["\']([0-9.]+)', logo_svg_content)
        vb_x, vb_y = 0.0, 0.0
        vb_w = float(w_m.group(1)) if w_m else 100.0
        vb_h = float(h_m.group(1)) if h_m else 100.0

    # ── Strip Inkscape-specific metadata that can cause rendering issues ─────
    # Remove <sodipodi:namedview> and <inkscape:page> elements which can cause
    # Inkscape to render extra pages when processing the final SVG.
    logo_content = re.sub(
        r'<sodipodi:namedview[^>]*>.*?</sodipodi:namedview>',
        '',
        logo_svg_content,
        flags=re.DOTALL,
    )
    logo_content = re.sub(
        r'<inkscape:page[^>]*/>',
        '',
        logo_content,
    )
    # Also remove any other Inkscape/Sodipodi namespace attributes from the root <svg>
    logo_content = re.sub(
        r'\s+(?:sodipodi|inkscape):[^=]*="[^"]*"',
        '',
        logo_content,
    )

    # ── Rename all IDs to avoid collisions with the template ────────────────
    # Collect IDs in order of appearance; process longest first to avoid
    # accidentally replacing a short ID that appears as a substring of a longer one.
    ids = list(dict.fromkeys(re.findall(r'\bid=["\']([^"\']+)["\']', logo_content)))
    for oid in sorted(ids, key=len, reverse=True):
        nid = f'{uid}_{oid}'
        logo_content = re.sub(
            rf'\bid=["\']({re.escape(oid)})["\']',
            f'id="{nid}"',
            logo_content,
        )
        logo_content = logo_content.replace(f'url(#{oid})', f'url(#{nid})')
        logo_content = re.sub(
            rf'(xlink:href|href)=["\']#{re.escape(oid)}["\']',
            lambda m, n=nid: f'{m.group(1)}="#{n}"',
            logo_content,
        )

    # ── Separate <defs> from body ────────────────────────────────────────────
    defs_m = re.search(r'<defs\b[^>]*>(.*?)</defs>', logo_content, re.DOTALL)
    defs_content = defs_m.group(1).strip() if defs_m else ''

    # ── Fix CSS classes that don't have fill defined ──────────────────────────
    # Some logos (like Astea) have CSS classes without fill, which can inherit
    # the parent's fill color (e.g., white from the logo box). Add explicit
    # black fill to classes that don't have one defined anywhere in the stylesheet.
    if defs_content:
        # First, collect all class names that DO have fill defined in their own rules
        classes_with_fill = set()
        fill_pattern = r'\.([a-zA-Z0-9_-]+)\s*\{[^}]*fill\s*:'
        for match in re.finditer(fill_pattern, defs_content):
            classes_with_fill.add(match.group(1))
        
        # Now fix rules that don't have fill and contain classes that need it
        def fix_class_rule(match):
            full_match = match.group(0)
            selector = match.group(1)  # e.g., ".cls-1" or ".cls-1, .cls-2, .cls-3"
            rule_body = match.group(2)  # content between { }
            
            # Skip if this rule already has fill
            if 'fill:' in rule_body or 'fill ' in rule_body:
                return full_match
            
            # Extract individual class names from the selector
            class_names = re.findall(r'\.([a-zA-Z0-9_-]+)', selector)
            
            # Check if at least one class in this selector needs fill
            # (i.e., doesn't have fill defined in a later rule)
            needs_fill = any(cls not in classes_with_fill for cls in class_names)
            
            if needs_fill:
                # Add fill: #000 before the closing brace
                # Preserve indentation from the rule body
                indent = ' ' * (len(rule_body) - len(rule_body.lstrip())) if rule_body.strip() else '      '
                return f'{selector} {{\n{rule_body.rstrip()}\n{indent}fill: #000;\n      }}'
            return full_match
        
        # Match CSS rules (handles both single and comma-separated selectors)
        # Pattern matches: .class or .class1, .class2, .class3 followed by { ... }
        rule_pattern = r'((?:\.\s*[a-zA-Z0-9_-]+\s*(?:,\s*\.\s*[a-zA-Z0-9_-]+)*))\s*\{([^}]*)\}'
        defs_content = re.sub(rule_pattern, fix_class_rule, defs_content, flags=re.MULTILINE | re.DOTALL)

    # Body: everything between the outer <svg> tags, minus the <defs> block
    body = re.sub(r'<svg\b[^>]*>', '', logo_content)
    body = re.sub(r'</svg\s*>', '', body)
    body = re.sub(r'<defs\b[^>]*>.*?</defs>', '', body, flags=re.DOTALL)
    body = body.strip()

    # ── Build a <g> group for each box ──────────────────────────────────────
    padding = 10
    groups = []
    for rect_id, (bx, by, bw, bh, flipped) in _LOGO_BOXES.items():
        avail_w = bw - 2 * padding
        avail_h = bh - 2 * padding
        s = min(avail_w / vb_w, avail_h / vb_h)   # uniform scale

        # Center of box in global SVG coordinates
        cx = bx + bw / 2
        cy = by + bh / 2

        # Center of the logo in its own coordinate space
        logo_cx = vb_x + vb_w / 2
        logo_cy = vb_y + vb_h / 2

        if flipped:
            # Rotate 180° (flip both axes) around the box center so the logo
            # appears right-side-up when the badge is viewed from the back.
            #
            # Transform chain:  T(tx, ty) · S(-s, -s)
            # A logo point (px, py) maps to:  (tx - px·s,  ty - py·s)
            # To land the logo center on the box center:
            #   tx - logo_cx · s = cx   →   tx = cx + logo_cx · s
            #   ty - logo_cy · s = cy   →   ty = cy + logo_cy · s
            tx = cx + logo_cx * s
            ty = cy + logo_cy * s
            transform = f'translate({tx:.4f},{ty:.4f}) scale({-s:.6f})'
        else:
            # Normal orientation: shift so the (potentially non-zero) viewBox
            # origin lines up with the box interior, then center the result.
            #
            # Top-left of the scaled logo should be at:
            #   (bx + (bw - vb_w·s)/2,  by + (bh - vb_h·s)/2)
            # To account for a non-zero viewBox offset we subtract (vb_x·s, vb_y·s).
            tx = bx + (bw - vb_w * s) / 2 - vb_x * s
            ty = by + (bh - vb_h * s) / 2 - vb_y * s
            transform = f'translate({tx:.4f},{ty:.4f}) scale({s:.6f})'

        safe_id = rect_id.replace(' ', '_')
        groups.append(
            f'<g id="logo_{safe_id}_{uid}" transform="{transform}">\n'
            f'{body}\n'
            f'</g>'
        )

    # ── Inject logo defs into the template's <defs> section ─────────────────
    if defs_content:
        last_defs_pos = svg_content.rfind('</defs>')
        if last_defs_pos != -1:
            svg_content = (
                svg_content[:last_defs_pos]
                + defs_content + '\n'
                + svg_content[last_defs_pos:]
            )
        else:
            # Template has no <defs>; create one before </svg>
            svg_content = svg_content.replace(
                '</svg>',
                f'<defs>\n{defs_content}\n</defs>\n</svg>',
                1,
            )

    # ── Inject logo <g> groups right after the white-box <rect> elements ────
    # Rectangle 342 is the last rect before the closing </g> of the main group.
    logo_markup = '\n'.join(groups)
    svg_content = re.sub(
        r'(<rect\s+id="Rectangle 342"[^>]*/>\s*)(</g>)',
        lambda m: m.group(1) + logo_markup + '\n' + m.group(2),
        svg_content,
        flags=re.DOTALL,
    )

    return svg_content


def get_raster_dimensions(file_path):
    """Return (width, height) in pixels for a PNG or WebP file.

    Parses the binary header directly so Pillow is not required.
    Falls back to Pillow if the format is not recognised.
    """
    with open(file_path, 'rb') as f:
        header = f.read(30)

        # ── PNG ───────────────────────────────────────────────────────────────────
        if header[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = struct.unpack('>II', header[16:24])
            return w, h

        # ── WebP ──────────────────────────────────────────────────────────────────
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
            chunk_type = header[12:16]
            
            if chunk_type == b'VP8 ':    # lossy
                # Read more bytes to get to the frame header
                f.seek(0)
                data = f.read(50)
                if len(data) >= 30:
                    # VP8 frame header: 3-byte sync code, then dimensions
                    # Dimensions are at offset 26-29 (after RIFF/WEBP/chunk header)
                    w = struct.unpack_from('<H', data, 26)[0] & 0x3FFF
                    h = struct.unpack_from('<H', data, 28)[0] & 0x3FFF
                    return w, h
            
            elif chunk_type == b'VP8L':  # lossless
                # Read more bytes to get the VP8L chunk data
                f.seek(0)
                data = f.read(25)
                if len(data) >= 25:
                    # VP8L: after chunk header (12 bytes), first 4 bytes contain dimensions
                    bits = struct.unpack_from('<I', data, 21)[0]
                    w = (bits & 0x3FFF) + 1
                    h = ((bits >> 14) & 0x3FFF) + 1
                    return w, h
            
            elif chunk_type == b'VP8X':  # extended
                # Read more bytes to get VP8X chunk data
                f.seek(0)
                data = f.read(30)
                if len(data) >= 30:
                    # VP8X: after chunk header (12 bytes), flags (1 byte), then dimensions
                    # Width: 3 bytes at offset 24-26 (24-bit little-endian, value is width-1)
                    # Height: 3 bytes at offset 27-29 (24-bit little-endian, value is height-1)
                    w = (data[24] | (data[25] << 8) | (data[26] << 16)) + 1
                    h = (data[27] | (data[28] << 8) | (data[29] << 16)) + 1
                    return w, h

    # ── Fallback: try Pillow ──────────────────────────────────────────────────
    try:
        from PIL import Image
        with Image.open(file_path) as img:
            return img.size
    except (ImportError, Exception):
        return 100, 100


def embed_raster_logos_in_svg(svg_content, logo_path, mime_type):
    """
    Embed a raster (PNG/WebP) logo into both white logo boxes as a base64 data URI.

    Uses the same box layout and scaling logic as embed_logos_in_svg:
      - Rectangle 341 – bottom half, normal orientation
      - Rectangle 342 – top half,    flipped 180°

    The logo is scaled uniformly to fit inside each box with 10 px padding and
    centered horizontally and vertically.

    Note: WebP is converted to PNG for better SVG renderer compatibility (many
    renderers, including Inkscape, don't support WebP data URIs).
    """
    _embed_logo_call_count[0] += 1
    uid = f'lg{_embed_logo_call_count[0]:04d}'

    img_w, img_h = get_raster_dimensions(logo_path)

    # Convert WebP to PNG if Pillow is available (for better SVG renderer support)
    if mime_type == 'image/webp':
        try:
            from PIL import Image
            import io
            with Image.open(logo_path) as img:
                # Convert to RGBA if needed, then to PNG
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                png_buffer = io.BytesIO()
                img.save(png_buffer, format='PNG')
                logo_bytes = png_buffer.getvalue()
            mime_type = 'image/png'  # Use PNG data URI instead
        except (ImportError, Exception):
            # Fall back to WebP if conversion fails
            with open(logo_path, 'rb') as f:
                logo_bytes = f.read()
    else:
        with open(logo_path, 'rb') as f:
            logo_bytes = f.read()

    data_uri = (
        f'data:{mime_type};base64,'
        + base64.b64encode(logo_bytes).decode('ascii')
    )

    padding = 10
    groups = []
    for rect_id, (bx, by, bw, bh, flipped) in _LOGO_BOXES.items():
        avail_w = bw - 2 * padding
        avail_h = bh - 2 * padding
        s = min(avail_w / img_w, avail_h / img_h)

        logo_cx = img_w / 2
        logo_cy = img_h / 2
        cx = bx + bw / 2
        cy = by + bh / 2

        if flipped:
            # 180° flip around the box centre — same maths as the SVG version
            tx = cx + logo_cx * s
            ty = cy + logo_cy * s
            transform = f'translate({tx:.4f},{ty:.4f}) scale({-s:.6f})'
        else:
            tx = bx + (bw - img_w * s) / 2
            ty = by + (bh - img_h * s) / 2
            transform = f'translate({tx:.4f},{ty:.4f}) scale({s:.6f})'

        safe_id = rect_id.replace(' ', '_')
        groups.append(
            f'<g id="logo_{safe_id}_{uid}" transform="{transform}">\n'
            f'<image x="0" y="0" width="{img_w}" height="{img_h}" '
            f'href="{data_uri}" preserveAspectRatio="xMidYMid meet"/>\n'
            f'</g>'
        )

    logo_markup = '\n'.join(groups)
    svg_content = re.sub(
        r'(<rect\s+id="Rectangle 342"[^>]*/>\s*)(</g>)',
        lambda m: m.group(1) + logo_markup + '\n' + m.group(2),
        svg_content,
        flags=re.DOTALL,
    )

    return svg_content


def chunked(iterable, size):
    for i in range(0, len(iterable), size):
        yield iterable[i:i + size]


def generate_svg(name, subtitle, logo_path=None, filename=None):
    if filename is None:
        filename = name
    filename = f'svg/{filename}.svg'
    ensure_tree_exists(filename)
    if not os.path.exists(filename) or overwrite:
        result = replace_text_with_html_support(template, replace_text, name)
        result = replace_text_with_html_support(result, subtitle_replace_text, subtitle)
        # Center the text after replacement
        result = center_text_in_svg(result, name)
        if subtitle != subtitle_replace_text:
            result = center_text_in_svg(result, subtitle)
        # Embed logo into both white boxes if a logo path was provided
        if logo_path:
            try:
                ext = os.path.splitext(logo_path)[1].lower()
                if ext == '.svg':
                    with open(logo_path, encoding='utf-8') as lf:
                        logo_svg = lf.read()
                    result = embed_logos_in_svg(result, logo_svg)
                elif ext == '.png':
                    result = embed_raster_logos_in_svg(result, logo_path, 'image/png')
                elif ext == '.webp':
                    result = embed_raster_logos_in_svg(result, logo_path, 'image/webp')
                else:
                    sys.stderr.write(
                        f'Warning: unsupported logo format "{ext}" for "{logo_path}"\n'
                    )
            except (OSError, IOError) as e:
                sys.stderr.write(f'Warning: could not read logo "{logo_path}": {e}\n')
        with open(filename, 'w', encoding='utf-8') as output_file:
            output_file.write(result)
    return filename


def batch_process(svg_files, export_png=False, export_pdf=False, chunk_size=20):
    """
    Convert text to path and export PNG/PDF in one Inkscape launch per chunk.
    """

    if not svg_files:
        return

    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

    for group in chunked(svg_files, chunk_size):

        actions = []

        for svg_path in group:
            abs_svg = os.path.abspath(svg_path)
            base, _ = os.path.splitext(abs_svg)

            # --- OPEN FILE ---
            actions.append(f"file-open:{abs_svg}")

            # --- CONVERT TEXT TO PATH ---
            actions.extend([
                "select-all",
                "object-to-path",
                "export-type=svg",
                f"export-filename:{abs_svg}",
                "export-do",
            ])

            # --- EXPORT PNG ---
            if export_png:
                png_path = base.replace(os.sep + "svg" + os.sep,
                                        os.sep + "png" + os.sep) + ".png"
                os.makedirs(os.path.dirname(png_path), exist_ok=True)

                actions.extend([
                    "export-area-drawing",
                    "export-type=png",
                    f"export-filename:{png_path}",
                    "export-do",
                ])

            # --- EXPORT PDF ---
            if export_pdf:
                pdf_path = base.replace(os.sep + "svg" + os.sep,
                                        os.sep + "pdf" + os.sep) + ".pdf"
                os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

                actions.extend([
                    "export-area-drawing",
                    "export-type=pdf",
                    f"export-filename:{pdf_path}",
                    "export-do",
                ])

            # --- CLOSE FILE (CRITICAL) ---
            actions.append("file-close")

        cmd = [
            inkscape_path,
            "--batch-process",
            f"--actions={';'.join(actions)}",
        ]

        subprocess.check_call(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags
        )


def main():
    parser = argparse.ArgumentParser()

    global inkscape_path
    global replace_text
    global subtitle_replace_text
    global overwrite

    parser.add_argument('template', type=argparse.FileType('r', encoding='utf-8'))
    parser.add_argument('--input-file', type=argparse.FileType('r', encoding='utf-8'), default='-')
    parser.add_argument('--inkscape-path', default=inkscape_path)
    parser.add_argument('--replace-text', default=replace_text)
    parser.add_argument('--subtitle-replace-text', default=subtitle_replace_text)
    parser.add_argument('--output-dir', default='output')
    parser.add_argument('--logos-dir', default='logos',
                        help='Directory to search for logo SVG files (default: logos/)')
    parser.add_argument('--pdf', action='store_true')
    parser.add_argument('--png', action='store_true')
    parser.add_argument('--overwrite', action='store_true', default=overwrite)

    args = parser.parse_args()

    global template
    template = args.template.read()

    inkscape_path = args.inkscape_path
    replace_text = args.replace_text
    subtitle_replace_text = args.subtitle_replace_text
    overwrite = args.overwrite

    # Remember the original working directory so logo paths can be resolved
    # correctly even after os.chdir(args.output_dir) below.
    original_dir = os.path.abspath(os.getcwd())
    logos_dir    = os.path.join(original_dir, args.logos_dir)

    os.makedirs(args.output_dir, exist_ok=True)
    os.chdir(args.output_dir)

    seen_names = {}
    saved_names_meta = []

    names = args.input_file.read().splitlines()

    all_svg_files = []

    for name in tqdm(names):
        name = name.strip()
        if not name:
            continue

        logo_path = None
        subtitle  = subtitle_replace_text
        meta      = {}

        if '\t' in name:
            name, logo_field = name.split('\t', 1)
            logo_field = logo_field.strip()

            # Try to find the logo file via several candidate paths.
            # If none exist, fall back to treating the field as subtitle text
            # (backward compatibility with the mentor template).
            candidates = [
                logo_field,
                os.path.join(original_dir, logo_field),
                os.path.join(logos_dir, logo_field),
                logo_field + '.svg',
                os.path.join(original_dir, logo_field + '.svg'),
                os.path.join(logos_dir, logo_field + '.svg'),
                logo_field + '.png',
                os.path.join(original_dir, logo_field + '.png'),
                os.path.join(logos_dir, logo_field + '.png'),
                logo_field + '.webp',
                os.path.join(original_dir, logo_field + '.webp'),
                os.path.join(logos_dir, logo_field + '.webp'),
            ]
            for candidate in candidates:
                if os.path.isfile(candidate):
                    logo_path = os.path.abspath(candidate)
                    break

            if logo_path is None:
                # Not a file path — treat as subtitle replacement text
                subtitle = logo_field
                meta['subtitle'] = logo_field

        filename = name
        if filename in seen_names:
            seen_names[filename] += 1
            filename = f'{filename} ({seen_names[filename]})'
        seen_names[filename] = 0

        svg_path = generate_svg(name, subtitle, logo_path=logo_path, filename=filename)

        meta.update({
            'name': name,
            'logo': logo_path,
            'svg': svg_path,
            'pdf': None,
            'png': None,
        })

        all_svg_files.append(svg_path)
        saved_names_meta.append(meta)

    batch_process(
        all_svg_files,
        export_png=args.png,
        export_pdf=args.pdf,
        chunk_size=20
    )

    json.dump(saved_names_meta, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
