#include <iostream>
#include <fstream>
#include <unistd.h>
#include "node.h"
#include "json_label.h"
#include "unit_cost_model.h"
#include "label_intersection.h"
#include "bracket_notation_parser.h"
#include "tree_indexer.h"
#include "jedi_baseline_index.h"
#include "quickjedi_index.h"
#include "wang_index.h"
#include "jofilter_index.h"
#include "scan.h"
#include "index.h"
#include "two_stage_inverted_list.h"
#include "label_set_converter.h"
#include "label_set_element.h"
#include "lookup_result_element.h"
#include <vector>
#include <string>
#include <filesystem>
#include <unistd.h>
#include <cJSON.h> 
#include "json_to_bracket.h"
#include <Eigen/Dense>
#include "ortools/graph/assignment.h"

#include "n_way_match.h"

namespace fs = std::filesystem;

std::string read_file(const std::string& filename) {
    std::ifstream file(filename);
    if (!file.is_open()) {
        throw std::runtime_error("Cannot open file: " + filename);
    }
    
    std::string content((std::istreambuf_iterator<char>(file)),
                        std::istreambuf_iterator<char>());
    return content;
}

// Function to get all JSON files from a directory
std::vector<std::pair<std::string, std::string>> get_json_files(const std::string& directory_path) {
    std::vector<std::pair<std::string, std::string>> json_files;
    
    try {
        for (const auto& entry : fs::directory_iterator(directory_path)) {
            if (entry.is_regular_file()) {
                std::string filename = entry.path().string();
                std::string extension = entry.path().extension().string();
                
                // Check if file has .json extension (case insensitive)
                if (extension == ".json" || extension == ".JSON") {
                    json_files.push_back({filename, read_file(filename)});
                    // std::cout << "Found JSON file: " << filename << std::endl;
                }
            }
        }
    } catch (const fs::filesystem_error& ex) {
        std::cerr << "Error accessing directory " << directory_path << ": " << ex.what() << std::endl;
    }
    
    return json_files;
}

std::vector<std::vector<std::string>> prepare_json_documents(std::vector<std::pair<std::string, std::string>>& json_files)
{
    std::vector<std::vector<std::string>> prepared_documents;
    
    for (const std::pair<std::string, std::string>& json_string : json_files) {
        // first is file path, second is json as string
        cJSON* root = cJSON_Parse(json_string.second.c_str());
        if (!root) {
            const char* error_ptr = cJSON_GetErrorPtr();
            if (error_ptr != NULL) {
                std::cerr << "Error before: " << error_ptr << std::endl;
            }
            continue;
        }
        
        std::vector<std::string> components;
        cJSON* components_array = cJSON_GetObjectItem(root, "components");
        if (cJSON_IsArray(components_array)) {
            int array_size = cJSON_GetArraySize(components_array);
            
            for (int i = 0; i < array_size; i++) {
                cJSON* component = cJSON_GetArrayItem(components_array, i);
                if (component) {
                    char* component_string = cJSON_Print(component);
                    if (component_string) {
                        components.push_back(json_to_bracket(std::string(component_string)));
                        free(component_string); 
                    }
                }
            }
        }
        prepared_documents.push_back(components);
        cJSON_Delete(root);
    }
    
    return prepared_documents;
}


std::vector<Match> n_way_match(std::string& json_directory)
{
    std::vector<std::pair<std::string, std::string>> json_files = get_json_files(json_directory);
    
    if (json_files.empty()) {
        std::cout << "No JSON files found in " << json_directory << std::endl;
        return std::vector<Match>();
    }

    return n_way_match(json_files);
}

std::vector<Match> n_way_match(std::vector<std::string>& json_documents)
{
    std::vector<std::pair<std::string, std::string>> input_adated;

    for (int i = 0; i < json_documents.size(); i++)
    {
        input_adated.push_back({"", json_documents[i]});
    }
    return n_way_match(input_adated);
}

std::vector<Match> n_way_match(std::vector<std::pair<std::string, std::string>>& json_documents)
{
    // Definitions
    using Label = label::JSONLabel;
    using LabelSetElem = label_set_converter_index::LabelSetElement;
    using CostModel = cost_model::UnitCostModelJSON<Label>;
    using LabelDictionary = label::LabelDictionary<Label>;
    using TreeIndexer = node::TreeIndexJSON;
    using JEDIBASE = json::JEDIBaselineTreeIndex<CostModel, TreeIndexer>;
    using JOFILTER = json::JOFilterTreeIndex<CostModel, TreeIndexer>;

    const double MaxCost = 1e9;
    
    LabelDictionary ld;
    CostModel ucm(ld);
    JOFILTER jofilter_algorithm(ucm);
    JEDIBASE baseline_algorithm(ucm);

    std::vector<lookup::LookupResultElement> jsim_baseline;

    parser::BracketNotationParser<Label> bnp;
    double distance_threshold = 100000;
    
    // Export components from json and process documents to bracket style -> list of list of bracket strings 
    std::vector<std::vector<std::string>> documents = prepare_json_documents(json_documents);
    
    int pivot_index = 0;
    int pivot_size = 0;
    int nr_documents = documents.size();
    for (int i = 0; i < nr_documents; i++) {
        std::vector<std::string>& document_i = documents[i];
        if (document_i.size() > pivot_size) {
            pivot_index = i;
            pivot_size = document_i.size();
        }
    }

    // k : document index
    // i : pivot components entry index
    // j : current document components entry index
    std::vector<Match> matching;

    std::vector<std::string>& pivot_document = documents[pivot_index];
    for (int k = 0; k < nr_documents; k++) {
        if (k == pivot_index) {
            continue;
        }
        std::vector<std::string>& target_document = documents[k];
        int target_size = target_document.size();
        int pivot_size = pivot_document.size();
        Eigen::MatrixXd Cost = Eigen::MatrixXd::Constant(pivot_size, pivot_size, MaxCost);

        for (int i = 0; i < pivot_size; i++) {
            std::vector<node::Node<Label>> trees_collection;
            std::string pivot_bracket_string = pivot_document[i];
            trees_collection.push_back(bnp.parse_single(pivot_bracket_string));

            // adding all queries to the trees_collection
            for (int j = 0; j < target_size; j++) {
                // distance via jedi
                std::string target_bracket_string = target_document[j];
                trees_collection.push_back(bnp.parse_single(target_bracket_string));
            }

            long int collection_size = trees_collection.size();

            std::vector<std::pair<int, std::vector<LabelSetElem>>> sets_collection;
            std::vector<std::pair<int, int>> size_setid_map;
            label_set_converter_index::Converter<Label> lsc;
            lsc.assignFrequencyIdentifiers(trees_collection, sets_collection, size_setid_map);
            unsigned int label_cnt = lsc.get_number_of_labels();

            lookup::TwoStageInvertedList tsil(label_cnt);
            tsil.build(sets_collection);
            
            lookup::VerificationIndex<Label, JEDIBASE> id;
            // Jedi algorithm
            jsim_baseline = id.execute_lookup(trees_collection, sets_collection, size_setid_map, tsil, 0, distance_threshold);

            for (const auto &res : jsim_baseline) {
                if (res.tree_id_1 != 0) continue; // sanity
                int cand_index = res.tree_id_2; // index into trees_collection
                int j = cand_index - 1; // targets start at index 1:
                if (j >= 0 && j < target_size) {
                    Cost(i, j) = res.jedi_value;
                }
            }
        }

        // output matrix is pivot_size x target_size matrix
        operations_research::SimpleLinearSumAssignment solver;

        for (int i = 0; i < pivot_size; i++) {
            for (int j = 0; j < pivot_size; j++) {
                solver.AddArcWithCost(i, j, Cost(i,j));
            }
        }
        
        // optimise for best matching
        operations_research::SimpleLinearSumAssignment::Status status = solver.Solve();
        if (status == operations_research::SimpleLinearSumAssignment::OPTIMAL) {
            for (int i = 0; i < pivot_size; i++) {
                int j = solver.RightMate(i);
                if (j >= 0 && j < target_size) {
                    Match m{
                        pivot_index, 
                        k, 
                        json_documents[pivot_index].first,
                        json_documents[k].first,
                        i, 
                        j, 
                        Cost(i, j)};
                    matching.push_back(m);
                }
            }
        }
    }
    // std::cout << "(doc, comp) - (doc, comp)" << std::endl;
    // for (auto& m: matching) {
    //     std::cout << "(" << m.query_doc << ", " << m.query_comp << ") - (" << m.target_doc << ", " << m.target_comp << "), cost: " << m.cost << std::endl;
    // }

    return matching;
}