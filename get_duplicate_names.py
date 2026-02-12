import argparse
from collections import Counter

parser = argparse.ArgumentParser()
parser.add_argument('input_file', type=argparse.FileType('r'), default='-')

args = parser.parse_args()

names = []
for line in args.input_file:
    name = line.strip()
    if '\t' in name:
        name, subtitle = name.split('\t', 1)
    names.append(name)

c = Counter(names)
for name, count in c.items():
    if count > 1:
        print(name)
