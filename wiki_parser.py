import re
import argparse

class WikidotParser:
    def __init__(self):
        self.errors = []
        
        # Inline replacements
        self.inline_rules = [
            (re.compile(r'\*\*([^\n]*?)\*\*'), r'<strong>\1</strong>'),
            (re.compile(r'//([^\n]*?)//'), r'<em>\1</em>'),
            (re.compile(r'__([^\n]*?)__'), r'<u>\1</u>'),
            (re.compile(r'--([^\n]*?)--'), r'<strike>\1</strike>'),
            (re.compile(r'\^\^([^\n]*?)\^\^'), r'<sup>\1</sup>'),
            (re.compile(r',,([^\n]*?),,'), r'<sub>\1</sub>'),
            (re.compile(r'\{\{([^\n]*?)\}\}'), r'<code>\1</code>'),
            # Wikilink: [[[page|text]]]
            (re.compile(r'\[\[\[([^|\]]+)\|([^\]]+)\]\]\]'), r'<a href="/\1">\2</a>'),
            # URL explicit: [http://url text]
            (re.compile(r'\[(https?://[^\s\]]+) ([^\]]+)\]'), r'<a href="\1">\2</a>'),
            # URL implicit (won\'t match inside already processed brackets)
            (re.compile(r'(?<!\[)(https?://[a-zA-Z0-9./_\-%?=&]+)'), r'<a href="\1">\1</a>'),
        ]
        
        self.html_re = re.compile(r'<html\s*>|</html\s*>|<(/?)script.*?>', re.IGNORECASE)

    def parse(self, text: str) -> tuple[str, list[str]]:
        self.errors = []
        if self.html_re.search(text):
            self.errors.append("Validation Error: Raw HTML or scripts are not allowed. Found restricted tags.")
            # Escape them
            text = text.replace('<html', '&lt;html').replace('</html', '&lt;/html')
            
        stack = []
        lines = text.split('\n')
        out_lines = []
        
        block_open_re = re.compile(r'^\[\[([A-Za-z0-9_-]+)(?:\s+(.*?))?\]\]$')
        block_close_re = re.compile(r'^\[\[/([A-Za-z0-9_-]+)\]\]$')

        in_table = False
        in_list = False
        
        for i, line in enumerate(lines):
            line_num = i + 1
            stripped = line.strip()

            # Handle block closes
            match_close = block_close_re.match(stripped)
            if match_close:
                tag = match_close.group(1).lower()
                
                # Wikidot module's variability means we might see `[[/module]]` even if treated as self-closing
                if tag == 'module' and ('module' not in stack):
                    continue

                if not stack:
                    self.errors.append(f"Line {line_num}: Unexpected closing tag [[/{tag}]].")
                elif stack[-1] != tag:
                    self.errors.append(f"Line {line_num}: Mismatched closing tag. Expected [[/{stack[-1]}]], got [[/{tag}]].")
                    if tag in stack:
                        # Auto-close everything up to the matching tag to recover
                        while stack and stack[-1] != tag:
                            popped = stack.pop()
                            out_lines.append(f"</{self._html_tag(popped)}>")
                        stack.pop()
                        out_lines.append(f"</{self._html_tag(tag)}>")
                else:
                    stack.pop()
                    out_lines.append(f"</{self._html_tag(tag)}>")
                continue

            # Handle block opens
            match_open = block_open_re.match(stripped)
            if match_open:
                tag = match_open.group(1).lower()
                attrs = match_open.group(2)
                
                # Known Wikidot self-closing blocks
                if tag not in ('image', 'iframe', 'module', 'module654', 'toc', 'include', 'clear'):
                    stack.append(tag)
                    html_tag = self._html_tag(tag)
                    attr_str = f" {attrs}" if attrs else ""
                    out_lines.append(f"<{html_tag}{attr_str}>")
                    continue
                else:
                    # Self-closing elements
                    out_lines.append(f"<{tag} attributes='{attrs}' />")
                    continue
            
            # Handle Raw Blocks @@...@@
            raw_blocks = []
            def repl_raw(m):
                raw_blocks.append(m.group(1).replace('<', '&lt;').replace('>', '&gt;'))
                return f"\x00RAW_{len(raw_blocks)-1}\x00"
            
            line = re.sub(r'@@(.*?)@@', repl_raw, line)
            
            # Simple inline checking (e.g., Bold ** count)
            # Count only non-overlapping pairs using len(re.findall)
            stars = len(re.findall(r'\*\*', line))
            if stars % 2 != 0:
                 self.errors.append(f"Line {line_num}: Unbalanced bold '**' marker.")

            for rule, replacement in self.inline_rules:
                line = rule.sub(replacement, line)

            # Restore Raw Blocks
            for j, raw_content in enumerate(raw_blocks):
                line = line.replace(f"\x00RAW_{j}\x00", raw_content)

            # Headings
            heading_match = re.match(r'^(\+{1,6})\s+(.*)$', line)
            if heading_match:
                level = len(heading_match.group(1))
                content = heading_match.group(2)
                out_lines.append(f"<h{level}>{content}</h{level}>")
                continue
                
            # Lists
            if line.startswith('* '):
                if not in_list:
                    out_lines.append("<ul>")
                    in_list = True
                out_lines.append(f"<li>{line[2:]}</li>")
                continue
            elif in_list:
                out_lines.append("</ul>")
                in_list = False

            # Tables
            if line.startswith('||') and line.endswith('||'):
                if not in_table:
                    out_lines.append("<table>")
                    in_table = True
                # Strip leading and trailing ||
                cells = [c.strip() for c in line.strip('||').split('||')]
                tr = "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
                out_lines.append(tr)
                continue
            elif in_table:
                out_lines.append("</table>")
                in_table = False

            out_lines.append(line)

        # Cleanup unclosed blocks at EOF
        if in_list:
            out_lines.append("</ul>")
        if in_table:
            out_lines.append("</table>")

        if stack:
            self.errors.append(f"Validation Error: Unclosed blocks at end of file: {', '.join(stack)}")
            while stack:
                tag = stack.pop()
                out_lines.append(f"</{self._html_tag(tag)}>")

        return '\n'.join(out_lines), self.errors

    def _html_tag(self, wiki_tag: str) -> str:
        mapping = {
            'div': 'div',
            'span': 'span',
            'table': 'table',
            'row': 'tr',
            'cell': 'td',
            'math': 'div',
        }
        return mapping.get(wiki_tag, wiki_tag)

import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Wikidot syntax parser and validator")
    parser.add_argument("input", help="Input wikidot file or directory containing .txt files")
    parser.add_argument("-o", "--output", help="Output html file (if single file) or output directory (if batch processing)")
    args = parser.parse_args()

    input_path = Path(args.input)
    
    if input_path.is_dir():
        if not args.output:
            print("Error: Output directory must be specified using -o when input is a directory.")
            sys.exit(1)
            
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        
        files_to_process = list(input_path.glob("*.txt"))
        if not files_to_process:
            print(f"No .txt files found in {args.input}")
            sys.exit(0)
            
        print(f"Found {len(files_to_process)} files. Beginning batch processing...")
        total_errors = 0
        failed_files = 0
        
        for file_path in files_to_process:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
                
            wp = WikidotParser()
            html, errors = wp.parse(text)
            out_file = out_dir / (file_path.stem + ".html")
            
            with open(out_file, 'w', encoding='utf-8') as f:
                f.write(html)
                
            if errors:
                print(f"[FAIL] {file_path.name}: {len(errors)} errors found.")
                for err in errors:
                    print(f"       - {err}")
                total_errors += len(errors)
                failed_files += 1
            else:
                print(f"[OK] {file_path.name}")
                
        print(f"\nBatch processing complete.")
        print(f"Processed {len(files_to_process)} files: {len(files_to_process) - failed_files} OK, {failed_files} Failed.")
        if total_errors > 0:
            print(f"Total Validation Errors: {total_errors}")
            sys.exit(1)
        else:
            sys.exit(0)
            
    else:
        out_path = args.output if args.output else "output.html"
        with open(input_path, 'r', encoding='utf-8') as f:
            text = f.read()

        wp = WikidotParser()
        html, errors = wp.parse(text)

        if errors:
            print("Validation Errors Found:")
            for err in errors:
                print(f" - {err}")
        else:
            print("Validation Passed. No errors.")

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"HTML output saved to {out_path}")
        if errors:
            sys.exit(1)

if __name__ == '__main__':
    main()
