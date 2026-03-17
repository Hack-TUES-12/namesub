#!/usr/bin/env python3

import argparse
import html
import json
import os
import re
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


def _parse_text_transform_x_offset(attrs):
    """
    Parse transform on <text> to compute local x so that global center = center_x.
    Returns (kind, value): ('none', None) | ('translate', tx) | ('matrix_flip', a).
    """
    transform_match = re.search(r'transform\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
    if not transform_match:
        return ('none', None)
    t = transform_match.group(1).strip()
    trans = re.match(r'translate\s*\(\s*([-\d.]+)\s*[, ]\s*[-\d.]+\s*\)', t)
    if trans:
        return ('translate', float(trans.group(1)))
    mat = re.match(r'matrix\s*\(\s*-1\s+0\s+0\s+-1\s+([-\d.]+)\s+[-\d.]+\s*\)', t)
    if mat:
        return ('matrix_flip', float(mat.group(1)))
    return ('none', None)


def center_text_in_svg(svg_content, text_content):
    """
    Center text in SVG by adding text-anchor="middle" and updating x coordinate.
    Finds the text/tspan element containing the text and centers it.
    Accounts for parent <text> transform so global horizontal center is correct.
    """
    # Calculate center x from viewBox or width
    viewbox_match = re.search(r'viewBox=["\']([^"\']+)["\']', svg_content)
    width_match = re.search(r'width=["\']([^"\']+)["\']', svg_content)
    center_x = None
    if viewbox_match:
        parts = viewbox_match.group(1).split()
        if len(parts) >= 3:
            center_x = float(parts[2]) / 2
    elif width_match:
        width_str = width_match.group(1).replace('px', '').strip()
        try:
            center_x = float(width_str) / 2
        except ValueError:
            pass
    if center_x is None:
        center_x = 297.5  # Default for A4 width (595/2)

    # Match each <text ...>...</text> block so we can use the parent's transform for local x
    text_block_pattern = re.compile(
        r'<text([^>]*)>(.*?)</text>',
        re.IGNORECASE | re.DOTALL
    )
    tspan_pattern = re.compile(
        r'(<tspan[^>]*x=["\'])([^"\']+)(["\'][^>]*>)([^<]*)(</tspan>)',
        re.IGNORECASE
    )

    def process_text_block(match):
        attrs = match.group(1)
        inner = match.group(2)
        try:
            unescaped = html.unescape(inner)
            if text_content not in unescaped and text_content not in inner:
                return match.group(0)
        except Exception:
            if text_content not in inner:
                return match.group(0)

        kind, value = _parse_text_transform_x_offset(attrs)
        if kind == 'none':
            local_x = center_x
        elif kind == 'translate':
            # global_x = tx + local_x => local_x = center_x - tx
            local_x = center_x - value
        else:
            # matrix(-1 0 0 -1 a b): global_x = a - local_x => local_x = a - center_x
            local_x = value - center_x

        def replace_tspan(m):
            cnt = m.group(4)
            try:
                u = html.unescape(cnt)
                if text_content not in u and text_content not in cnt:
                    return m.group(0)
            except Exception:
                if text_content not in cnt:
                    return m.group(0)
            return m.group(1) + str(local_x) + m.group(3) + m.group(4) + m.group(5)

        inner = tspan_pattern.sub(replace_tspan, inner)
        if 'text-anchor' not in (attrs or ''):
            attrs = attrs.rstrip('>') + ' text-anchor="middle">'
        # attrs may already end with '>' when we added text-anchor; avoid double >
        close_angle = '' if attrs.rstrip().endswith('>') else '>'
        return '<text' + attrs + close_angle + inner + '</text>'

    return text_block_pattern.sub(process_text_block, svg_content)

def chunked(iterable, size):
    for i in range(0, len(iterable), size):
        yield iterable[i:i + size]



def generate_svg(name, subtitle, filename=None):
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
        with open(filename, 'w', encoding='utf-8') as output_file:
            output_file.write(result)
        # Convert text to paths to ensure font independence
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

        meta = {}
        if '\t' in name:
            name, subtitle = name.split('\t', 1)
            meta['subtitle'] = subtitle
        else:
            subtitle = subtitle_replace_text

        filename = name
        if filename in seen_names:
            seen_names[filename] += 1
            filename = f'{filename} ({seen_names[filename]})'
        seen_names[filename] = 0

        svg_path = generate_svg(name, subtitle, filename)

        meta.update({
            'name': name,
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
