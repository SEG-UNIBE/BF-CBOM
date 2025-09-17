import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "build"))
from glob import glob

import json_matching  # noqa: F401  # pylint: disable=import-error,wrong-import-position

try:
    json_directory = "assets"

    files = []

    for jf in glob(f"{json_directory}/*.json"):
        with open(jf) as file:
            files.append(file.read())

    matches = json_matching.n_way_match(files)

    print(f"Found {len(matches)} matches:")
    for match in matches:
        print(f"  Doc {match.query_file.split('/')[-1]} comp {match.query_comp} -> Doc {match.target_file.split('/')[-1]} comp {match.target_comp}, cost: {match.cost}")
except Exception as e:
    print(f"Error: {e}")