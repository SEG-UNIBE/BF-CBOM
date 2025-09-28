#!/usr/bin/env python3
import logging
import os
import time
import multiprocessing
from pathlib import Path
import csv
import numpy as np
import redis
from sentence_transformers import SentenceTransformer

from common.config import REDIS_HOST, REDIS_PORT
from common.models import ComponentMatchJobInstruction, ComponentMatchJobResult
from common.cbom_analysis import find_components_list

NAME = "pyqun"
JOB_QUEUE = f"jobs:{NAME}"
RESULT_LIST = f"results:{NAME}"
TIMEOUT_SEC = int(os.getenv("CALC_TIMEOUT_SEC", "120"))
MODELS_CSV_PATH = os.getenv("PYQUN_MODELS_CSV", "/opt/RaQuN_Lab/pyqun_models.csv")

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
    

class BERTEmbedding(Vectorizer):
    def __init__(self):
        self.model = SentenceTransformer('all-MiniLM-L6-v2')  # Fast, small, good for most tasks
        self.vec_dim = self.model.get_sentence_embedding_dimension()

    def innit(self, m_s: 'ModelSet') -> None:
        pass

    def vectorize(self, element: 'Element'):
        # Combine all attribute values into a single, deterministically ordered string
        vals = (
            str(getattr(attr, 'value', attr)).lower()
            for attr in (getattr(element, 'attributes', []) or [])
        )
        attr_texts = sorted(vals)
        text = " ".join(attr_texts)
        vec = self.model.encode(text, show_progress_bar=False)
        return vec

    def dim(self):
        return self.vec_dim
    
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
        # Iterate attribute values in a deterministic order
        for val in sorted(
            (str(getattr(attr, 'value', attr)).lower() for attr in (getattr(element, 'attributes', []) or []))
        ):
            for c in val:
                if c in self.letter_indices:
                    vec[self.letter_indices[c]] += 1
        return vec

    def dim(self):
        return self.vec_dim


PyQuN_algo = VanillaRaQuN(
    "high_dim_raqun", 
    candidate_search=NNCandidateSearch(
        vectorizer=BERTEmbedding(),
        neighbourhood_size=3
    )
)


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

def _write_models_csv(models: dict, out_path: str) -> None:
    """Write model elements to CSV for debugging.

    Columns: model_id, element_id, element_name, attributes (semicolon-separated)
    """
    try:
        out_dir = os.path.dirname(out_path)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for mid, elements in sorted(models.items()):
                for e in elements:
                    name = getattr(e, "name", "")
                    eid = getattr(e, "ele_id", getattr(e, "ze_id", None))
                    attrs = []
                    for attr in getattr(e, "attributes", []) or []:
                        attrs.append(str(getattr(attr, "value", attr)))
                    writer.writerow([mid, eid, name, ";".join(sorted(attrs))])
        logger.info("Wrote PyQuN models CSV to %s", out_path)
    except Exception as err:
        logger.warning("Failed to write PyQuN models CSV to %s: %s", out_path, err)


def _convert_json_string_to_dict(documents: list[list[str]]) -> list[list[dict]]:
    out = []

    for doc in documents:
        doc_list = []
        for json_file in doc:
            json_dict = json.loads(json_file)
            doc_list.append(json_dict)
    
        out.append(doc_list)

    return out

def _run_raqun_with_order(json_files, order=None):
    if order is None:
        order = list(range(len(json_files)))
    shuffled_json_files = [json_files[i] for i in order]
    matches = _run_raqun(shuffled_json_files)

    return matches, order

def _run_raqun(json_files: list[str]):
    global PyQuN_algo
    models = {}
    logger.info("Preparing %d models for RaQuN...", len(json_files))
    # extract and convert to Model/Element/Attribute for RaQuN
    for e_id, json_file in enumerate(json_files):
        if len(json_file)>0:
            models[e_id] = []
            for comp_i, comp in enumerate(json_file):
                attr_list = []
                type = comp["type"]
                name = comp["name"]

                attr_list.append(type)
                attr_list.append(name)
                
                try:

                    assetType = comp["cryptoProperties"]["assetType"]
                    attr_list.append(assetType)

                    primitive = comp["cryptoProperties"]["algorithmProperties"]["primitive"]
                    attr_list.append(primitive)

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
    logger.info("Loaded %d models with total %d elements", len(model_list), sum(len(m.get_elements()) for m in model_list))
    # Debug CSV output (comment out the following line to disable)
    # _write_models_csv(models, MODELS_CSV_PATH)
    for i, model in enumerate(model_list):
        logger.info("Model %d:", i)
        for element in model.get_elements():
            logger.info("  Element: %s, Attributes: %s", element.name, [attr.value for attr in element.attributes])
    model_set = ModelSet(set(model_list))

    matches, _ = PyQuN_algo.match(model_set)

    return list(matches)


def _match_from_json_list(json_files: list[str]):
    json_files = _convert_json_string_to_dict(json_files)

    # Deterministic single pass using the given order
    n = len(json_files)
    order = list(range(n))
    logger.info(f"🐁 Running RaQuN on {n} document(s) (deterministic order)")
    matches_list, best_order = _run_raqun_with_order(json_files, order)

    grouped_elements = []

    for match in matches_list:
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


def _match_components(documents: list[list[str]]) ->  list[dict]:
    
    logger.info("Starting n-way component matching for %d documents...", len(documents))
    serialized = _match_from_json_list(documents)

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
        entry.components_as_json
        for entry in instruction.CbomJsons
        if entry.components_as_json
    ]

    # documents_raw = [
    #     entry.entire_json_raw
    #     for entry in instruction.CbomJsons
    #     if entry.entire_json_raw
    # ]
    
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