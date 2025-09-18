import logging
import os
import sys
from glob import glob

import json_matching  # type: ignore  # pylint: disable=import-error,wrong-import-position

NAME = os.path.basename(os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "build"))



def main():
    # TODO: implement proper Redis messaging interface to get diffing orders from coordinator

    try:
        json_directory = "assets"

        files = []

        for jf in glob(f"{json_directory}/*.json"):
            with open(jf) as file:
                files.append(file.read())

        matches = json_matching.n_way_match(files)

        print(f"Found {len(matches)} matches:")
        for match in matches:
            print(
                f"  Doc {match.query_file.split('/')[-1]} comp {match.query_comp} "
                f"-> Doc {match.target_file.split('/')[-1]} comp {match.target_comp}, cost: {match.cost}"
            )
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    logger.info("starting up...")
    main()
