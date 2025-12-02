import re

def autocorrect_diff(content: str) -> str:
    """自动修正diff格式错误"""
    if not content or '<<<<<<< SEARCH' not in content:
        return content

    lines = content.splitlines()
    corrected_lines = []
    
    in_diff_block = False
    separator_found = False

    for line in lines:
        stripped_line = line.strip()

        if stripped_line == '<<<<<<< SEARCH':
            if in_diff_block:
                if not separator_found:
                    corrected_lines.append('=======')
                corrected_lines.append('>>>>>>> REPLACE')
            
            in_diff_block = True
            separator_found = False
            corrected_lines.append(line)

        elif stripped_line == '=======':
            if in_diff_block:
                if not separator_found:
                    corrected_lines.append(line)
                    separator_found = True
            else:
                corrected_lines.append(line)

        elif stripped_line == '>>>>>>> REPLACE':
            if in_diff_block:
                if not separator_found:
                    corrected_lines.append('=======')
                
                corrected_lines.append(line)
                in_diff_block = False
                separator_found = False
            else:
                corrected_lines.append(line)
        else:
            corrected_lines.append(line)

    if in_diff_block:
        if not separator_found:
            corrected_lines.append('=======')
        corrected_lines.append('>>>>>>> REPLACE')

    return '\n'.join(corrected_lines)