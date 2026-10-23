"""Link only supplied citation IDs; leave code and unknown IDs unchanged."""
import re

GROUP = re.compile(r'\[((?:E\d+|DB\d+|\d+)(?:\s*[,，]\s*(?:E\d+|DB\d+|\d+))*)\](?!\()')

def cited_ids(answer):
    prose = re.split(r'(```.*?```|`[^`]*`)', answer, flags=re.S)[::2]
    return {item.strip() for part in prose for match in GROUP.finditer(part)
            for item in re.split(r'[,，]', match[1])}

def link_answer(answer, links):
    def group(match):
        ids = [x.strip() for x in re.split(r'[,，]', match[1])]
        return ' '.join(f'[{item}]({links[item]})' if item in links else f'[{item}]' for item in ids)
    parts = re.split(r'(```.*?```|`[^`]*`)', answer, flags=re.S)
    return ''.join(part if i % 2 else GROUP.sub(group, part) for i, part in enumerate(parts))


def display_markdown(text):
    """Streamlit escapes HTML in table cells; keep line breaks readable."""
    return '\n'.join(re.sub(r'<br\s*/?>', '；', line, flags=re.I)
                     if line.lstrip().startswith('|') else line
                     for line in text.split('\n'))
