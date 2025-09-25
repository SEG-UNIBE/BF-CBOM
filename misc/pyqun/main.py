#!/usr/bin/env python3
import logging
import os
import time
import multiprocessing
from pathlib import Path
import numpy as np
import redis
import random
import itertools

from common.config import REDIS_HOST, REDIS_PORT
from common.models import ComponentMatchJobInstruction, ComponentMatchJobResult
from common.cbom_analysis import find_components_list

NAME = "pyqun"
JOB_QUEUE = f"jobs:{NAME}"
RESULT_LIST = f"results:{NAME}"
TIMEOUT_SEC = int(os.getenv("CALC_TIMEOUT_SEC", "120"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


redis_client: redis.Redis | None = None

import sys
import string
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.absolute()))

from RaQuN_Lab.datamodel.modelset.ModelSet import ModelSet
from RaQuN_Lab.datamodel.modelset.Element import Element
from RaQuN_Lab.datamodel.modelset.attribute.DefaultAttribute import DefaultAttribute
from RaQuN_Lab.datamodel.modelset.Model import Model
from RaQuN_Lab.strategies.RaQuN.candidatesearch.NNCandidateSearch.NNCandidateSearch import NNCandidateSearch
from RaQuN_Lab.strategies.RaQuN.candidatesearch.NNCandidateSearch.vectorization.ZeroOneVectorization import ZeroOneVectorizer
from RaQuN_Lab.strategies.RaQuN.RaQuN import VanillaRaQuN
from RaQuN_Lab.strategies.RaQuN.candidatesearch.NNCandidateSearch.vectorization.Vectorizer import Vectorizer
from RaQuN_Lab.strategies.RaQuN.candidatesearch.NNCandidateSearch.vectorization.DimensionalityReduction.SVDReduction import SVDReduction
    
class LetterHistogramVectorizer(Vectorizer):
    def __init__(self):
        # Use lowercase English letters
        self.letters = string.ascii_lowercase
        self.letter_indices = {c: i for i, c in enumerate(self.letters)}
        self.vec_dim = len(self.letters)

    def innit(self, m_s: 'ModelSet') -> None:
        # No need to build a global attribute map, just set vector dimension
        pass

    def vectorize(self, element: 'Element'):
        vec = np.zeros(self.vec_dim)
        for attr in getattr(element, 'attributes', []):
            val = str(getattr(attr, 'value', attr)).lower()
            for c in val:
                if c in self.letter_indices:
                    vec[self.letter_indices[c]] += 1
        return vec

    def dim(self):
        return self.vec_dim



def DFS(sub_dict, prefix: list, results: list):
    """
    extracts all leaves from the json tree as a list of strings containing all from root to leaf 
    args:
        - dict: containing the current branch of the tree
        - list[string]: a list of key strings that lead to this branch
        - list[list[string]]: argument holding the paths from root to leaf
    returns:
        - a list of all the paths to the leaves
    """
    if isinstance(sub_dict, dict):
        # branch case
        for k in sub_dict.keys():
            prefix_list = prefix.copy()
            prefix_list.append(str(k))
            value = sub_dict[k]
            DFS(value, prefix_list, results)
    elif isinstance(sub_dict, list):
        # branch case (no key)
        for value in sub_dict:
            prefix_list = prefix.copy()
            DFS(value, prefix_list, results)
    else:
        # leaf case
        value = str(sub_dict)
        prefix.append(value)
        results.append(prefix)


def convert_json_string_to_dict(json_string_list: list[str]):
    out = []

    for f in json_string_list:
        json_dict = json.loads(f)
        out.append(json_dict)

    return out

def load_jsons_from_files_as_strings(json_files: list[str]):
    out = []
    for path in json_files:
        with open(path, "r+") as f:
            out.append(f.read())

    return out

def run_raqun_with_order(json_files, order=None):
    if order is None:
        order = list(range(len(json_files)))
    shuffled_json_files = [json_files[i] for i in order]
    matches = run_raqun(shuffled_json_files)

    return matches, order

def run_raqun(json_files: list[str]):
    models = {}

    for e_id, json_file in enumerate(json_files):
        comps = find_components_list(json_file)
        if (comps and len(comps)>0):
            models[e_id] = []
            for comp_i, comp in enumerate(comps):

                attr_list = []
                name = comp["name"]

                # if name in ["library", "framework"]:
                #     continue
                
                attr_list.append(name)

                type = comp["type"]
                attr_list.append(type)
                try:

                    assetType = comp["cryptoProperties"]["assetType"]
                    attr_list.append(assetType)

                    primitive = comp["cryptoProperties"]["algorithmProperties"]["primitive"]
                    attr_list.append(primitive)

                    # res = []
                    # DFS(comp["cryptoProperties"], [], res)
                    # for p in res:
                    #     attr = "_".join(p)
                    #     attr_list.append(attr)
                except:
                    pass
                attributes = set(DefaultAttribute(attr) for attr in attr_list)

                element = Element(name=name, ze_id=comp_i, attributes=attributes) # -> corresponds to a cbom component
                element.set_model_id(e_id)
                element.set_element_id(comp_i)
                models[e_id].append(element)
        
    model_list = []

    for model_id in models:
        model = Model(elements=set(models[model_id]), ze_id=model_id)
        model_list.append(model)

    model_set = ModelSet(set(model_list))
    algo = VanillaRaQuN("high_dim_raqun", candidate_search=NNCandidateSearch(vectorizer=LetterHistogramVectorizer()))

    matches, _ = algo.match(model_set)

    return list(matches)


def match_from_json_list(json_files: list[str]):
    json_files = convert_json_string_to_dict(json_files)

    best_nr_matches = 0
    best_match = None
    best_order = None

    # only works for few documents (exponential cost for iteration)
    n = len(json_files)
    
    for curr_round, order in enumerate(itertools.permutations(range(n))):
        # order = list(range(len(json_files)))
        # random.shuffle(order)
        logger.info(f"Running RaQuN permutation round: {curr_round}")
        
        matches_list, used_order = run_raqun_with_order(json_files, order)
        curr_nr_matches = sum([len(e) for match in matches_list for e in match.get_elements()])

        if best_nr_matches < curr_nr_matches:
            best_nr_matches = curr_nr_matches
            best_match = matches_list
            best_order = used_order

    grouped_elements = []

    for match in best_match:
        if hasattr(match, 'get_elements'):
            elements = match.get_elements()
            if elements and len(elements) > 1:
                group = []
                for e in elements:
                    file_id = e.model_id  
                    original_idx = best_order[file_id]
                    comp_id = e.ele_id 
                    try:
                        group.append({
                            "file": original_idx,
                            "component": comp_id,
                            "cost": 0.0
                        })
                    except:
                        logger.error(f"Error during extraction of component: {comp_id} from file: {file_id}")
                grouped_elements.append(group)

    return grouped_elements


def _match_components(documents: list[str]) ->  list[dict]:
    
    logger.info("Starting n-way component matching for %d documents...", len(documents))
    serialized = match_from_json_list(documents)

    logger.info("Found %d component match(es)", len(serialized))
    logger.info("Matches:\n%s", serialized)
    return serialized


def _run_match_with_timeout(func, args, timeout_seconds):
    """
    Run a function in a separate process with a timeout.
    """
    with multiprocessing.Pool(processes=1) as pool:
        result = pool.apply_async(func, args=(args,))
        try:
            return result.get(timeout=timeout_seconds)
        except multiprocessing.TimeoutError:
            logger.error("Component matching timed out after %d seconds.", timeout_seconds)
            # The pool is automatically terminated, which should kill the child process
            return None
        
def _handle_instruction(raw_payload: str) -> None:
    global redis_client

    try:
        instruction = ComponentMatchJobInstruction.from_json(raw_payload)
    except Exception as err:
        logger.error("Failed to decode ComponentMatchJobInstruction: %s", err, exc_info=True)
        return

    logger.info(
        "📨 Received job instruction for job %s (repo: %s)", instruction.job_id, instruction.repo_info.full_name
    )
    
    
    # Transform the list of CbomJson objects into a list of documents (list[str])
    documents = [
        entry.entire_json_raw
        for entry in instruction.CbomJsons
        if entry.entire_json_raw
    ]
    
    tools = [entry.tool for entry in instruction.CbomJsons if entry.components_as_json]
    if len(documents) < 2:
        logger.warning("Need at least two documents with components to match. Aborting.")
        insufficient_result = ComponentMatchJobResult(
            job_id=instruction.job_id,
            benchmark_id=instruction.benchmark_id,
            repo_full_name=instruction.repo_info.full_name,
            tools=tools,
            match_count=0,
            matches=[],
            duration_sec=0.0,
            status="error",
            error="Need at least two CBOM payloads to compute similarity",
        )
        if redis_client is not None:
            try:
                redis_client.rpush(RESULT_LIST, insufficient_result.to_json())
            except Exception as err:  # pragma: no cover
                logger.warning(
                    "Failed to persist insufficient-input result for job %s: %s",
                    instruction.job_id,
                    err,
                )
        return

    logger.info(
        "Processing job %s from benchmark %s with %d CBOM payload(s)",
        instruction.job_id,
        instruction.benchmark_id,
        len(documents),
    )

    status = "ok"
    error_msg: str | None = None
    matches: list = []

    start_time = time.perf_counter()

    match_results = None

    try:
         match_results = _run_match_with_timeout(
            _match_components, documents, timeout_seconds=TIMEOUT_SEC
        )
    except TimeoutError as err:
        status = "timeout"
        error_msg = str(err)
        logger.warning("Job %s timed out after %ss", instruction.job_id, TIMEOUT_SEC)
    except Exception as err:
        status = "error"
        error_msg = str(err)
        logger.error("json_matching failed for job %s: %s", instruction.job_id, err, exc_info=True)

    if status == "ok":
        if match_results is None:
            status = "timeout"
            error_msg = (
                f"Component matching timed out after {TIMEOUT_SEC} seconds"
            )
            matches = []
        else:
            matches = match_results
    else:
        matches = []

    duration = time.perf_counter() - start_time

    result_payload = ComponentMatchJobResult(
        job_id=instruction.job_id,
        benchmark_id=instruction.benchmark_id,
        repo_full_name=instruction.repo_info.full_name,
        tools=tools,
        match_count=len(matches),
        matches=matches,
        duration_sec=duration,
        status=status,
        error=error_msg,
    )

    try:
        if redis_client is not None:
            redis_client.rpush(RESULT_LIST, result_payload.to_json())
            logger.info(
                "📤 Sent job result for job %s (repo: %s)", result_payload.job_id, result_payload.repo_full_name
            )
    except Exception as err:  # pragma: no cover - best-effort persistence
        logger.warning("Failed to persist result for job %s: %s", instruction.job_id, err)


def main() -> None:
    global redis_client

    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    logger.info("%s listening for component match jobs... (queue: %s)", NAME, JOB_QUEUE)

    try:
        while True:
            try:
                _, raw_payload = redis_client.blpop(JOB_QUEUE)
            except redis.exceptions.RedisError as err:
                logger.error("Redis error while waiting for jobs: %s", err)
                time.sleep(1)
                continue
            if not raw_payload:
                continue
            _handle_instruction(raw_payload)
    except KeyboardInterrupt:
        logger.info("Received shutdown signal, stopping listener")


if __name__ == "__main__":
    logger.info("starting up...")
    main()
