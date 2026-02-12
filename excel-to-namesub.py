#!/usr/bin/env python3

import argparse
import sys

try:
    import pandas as pd
except ImportError:
    print("Error: pandas is required. Install it with: pip install pandas openpyxl", file=sys.stderr)
    sys.exit(1)


def excel_to_namesub_input(excel_file, output_file=None):
    """
    Convert Excel table with team names and participant names to namesub input format.
    
    Expected Excel format:
    - Column 1: Team names (may be merged across rows)
    - Column 2: Participant names (one per row)
    """
    # Read Excel file
    df = pd.read_excel(excel_file, header=None)
    
    # Get the two columns
    team_col = df.iloc[:, 0]  # First column (teams)
    name_col = df.iloc[:, 1]  # Second column (names)
    
    output_lines = []
    current_team = None
    
    for idx in range(len(df)):
        team_value = team_col.iloc[idx]
        name_value = name_col.iloc[idx]
        
        # Skip header row if it exists
        if pd.isna(team_value) and pd.isna(name_value):
            continue
        
        # If team column has a value (not NaN), update current team
        if pd.notna(team_value) and str(team_value).strip():
            current_team = str(team_value).strip()
        
        # If name column has a value, output it with current team
        if pd.notna(name_value) and str(name_value).strip():
            name = str(name_value).strip()
            if current_team:
                output_lines.append(f"{name}\t{current_team}")
            else:
                # If no team found yet, just output name (will use default subtitle)
                output_lines.append(name)
    
    # Write to file or stdout
    output_text = '\n'.join(output_lines)
    
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(output_text)
        print(f"Converted {len(output_lines)} entries to {output_file}")
    else:
        print(output_text)
    
    return output_lines


def main():
    parser = argparse.ArgumentParser(
        description='Convert Excel table with teams and names to namesub input format'
    )
    parser.add_argument('excel_file', help='Path to Excel file (.xlsx)')
    parser.add_argument('-o', '--output', help='Output file (default: stdout)')
    
    args = parser.parse_args()
    
    try:
        excel_to_namesub_input(args.excel_file, args.output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
