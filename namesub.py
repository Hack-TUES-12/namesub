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
inkscape_path = os.path.expanduser('~/Downloads/software/inkscape/squashfs-root/AppRun')
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


def center_text_in_svg(svg_content, text_content):
    """
    Center text in SVG by adding text-anchor="middle" and updating x coordinate.
    Finds the text/tspan element containing the text and centers it.
    Works with text in any format (plain, HTML entity encoded).
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
    
    # Try to find tspan containing the text - search for any tspan that might contain our text
    # We'll match any tspan and check if it contains our text (in any encoding)
    tspan_pattern = r'(<tspan[^>]*x=["\'])([^"\']+)(["\'][^>]*>)([^<]*)(</tspan>)'
    
    def replace_tspan_if_matches(match):
        tspan_content = match.group(4)
        # Check if this tspan contains our text (try unescaping to compare)
        try:
            unescaped_content = html.unescape(tspan_content)
            if text_content in unescaped_content or text_content in tspan_content:
                # This is our tspan, update x to center
                return match.group(1) + str(center_x) + match.group(3) + match.group(4) + match.group(5)
        except Exception:
            if text_content in tspan_content:
                return match.group(1) + str(center_x) + match.group(3) + match.group(4) + match.group(5)
        # Not our tspan, return unchanged
        return match.group(0)
    
    # Replace tspan x coordinate for matching tspan elements
    svg_content = re.sub(tspan_pattern, replace_tspan_if_matches, svg_content, flags=re.IGNORECASE | re.DOTALL)
    
    # Find parent text element and add text-anchor="middle"
    # Look for text elements that contain tspan elements we might have modified
    text_pattern = r'(<text)([^>]*>)(.*?</tspan>.*?)(</text>)'
    
    def add_text_anchor(match):
        text_start = match.group(1)
        text_attrs = match.group(2)
        text_content_part = match.group(3)
        text_end = match.group(4)
        
        # Check if this text element contains our text
        try:
            unescaped_content = html.unescape(text_content_part)
            if text_content not in unescaped_content and text_content not in text_content_part:
                return match.group(0)  # Not our text element, return unchanged
        except Exception:
            if text_content not in text_content_part:
                return match.group(0)  # Not our text element, return unchanged
        
        # Check if text-anchor already exists
        if 'text-anchor' not in text_attrs:
            # Add text-anchor="middle" before the closing >
            text_attrs = text_attrs.rstrip('>') + ' text-anchor="middle">'
        
        return text_start + text_attrs + text_content_part + text_end
    
    svg_content = re.sub(text_pattern, add_text_anchor, svg_content, flags=re.IGNORECASE | re.DOTALL)
    
    return svg_content


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
    return filename


def generate_file_format(name, file_extension, subtitle, filename=None):
    svg_filename = generate_svg(name, subtitle, filename)
    format_filename = (svg_filename
                       .replace('.svg', f'.{file_extension}')
                       .replace('svg/', f'{file_extension}/'))
    if os.path.exists(format_filename):
        if not overwrite:
            return format_filename
        else:
            os.remove(format_filename)
    ensure_tree_exists(format_filename)
    subprocess.check_call(
        [inkscape_path, svg_filename,
         '--export-area-drawing', '--batch-process',
         f'--export-type={file_extension}', f'--export-filename={format_filename}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return format_filename


def generate_png(name, subtitle, filename=None):
    return generate_file_format(name, 'png', subtitle, filename)


def generate_pdf(name, subtitle, filename=None):
    return generate_file_format(name, 'pdf', subtitle, filename)


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

        meta.update({
            'name': name,
            'svg': generate_svg(name, subtitle, filename),
            'pdf': None,
            'png': None,
        })

        if args.pdf:
            meta['pdf'] = generate_pdf(name, subtitle, filename)
            # print(meta['pdf'])
        if args.png:
            meta['png'] = generate_png(name, subtitle, filename)
            # print(meta['png'])

        saved_names_meta.append(meta)

    json.dump(saved_names_meta, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
