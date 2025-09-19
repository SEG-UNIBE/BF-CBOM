#pragma once

#include <iostream>
#include <fstream>
#include <unistd.h>
#include <vector>
#include <string>
#include <filesystem>
#include <unistd.h>
#include <cJSON.h> 
#include <Eigen/Dense>
#include <string>
#include <algorithm>
#include <cmath>
#include <unordered_set>
#include <unordered_map>
#include <unordered_set>
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
#include "ortools/graph/assignment.h"
#include "label_dictionary.h"
#include "json_to_bracket.h"
#include "json_label.h"
#include "n_way_match.h"

namespace fs = std::filesystem;


ComponentId UnionFind::find(const ComponentId& x) 
{
    if (parent.find(x) == parent.end()) {
        parent[x] = x; // Initialize parent to itself
        return x;
    }
    
    if (parent[x] == x) {
        return x;
    }
    
    // Path compression
    parent[x] = find(parent[x]);
    return parent[x];
}
    
void UnionFind::unite(const ComponentId& x, const ComponentId& y) {
    ComponentId px = find(x);
    ComponentId py = find(y);
    
    if (px == py) return;
    
    parent[px] = py;
}
    
std::vector<std::vector<ComponentId>> UnionFind::getConnectedComponents() {
    std::unordered_map<ComponentId, std::vector<ComponentId>, ComponentIdHash> groups;
    
    for (const auto& [comp, _] : parent) {
        ComponentId root = find(comp);
        groups[root].push_back(comp);
    }
    
    std::vector<std::vector<ComponentId>> result;
    for (const auto& [_, group] : groups) {
        result.push_back(group);
    }
    
    return result;
}

std::vector<std::vector<ComponentId>> buildComponentChains(const std::vector<Match>& matches) {
    UnionFind uf;
    
    // Build union-find structure from matches
    for (const Match& match : matches) {
        ComponentId comp1{match.query_doc, match.query_comp, match.cost};
        ComponentId comp2{match.target_doc, match.target_comp, match.cost};
        uf.unite(comp1, comp2);
    }
    
    return uf.getConnectedComponents();
}


template <typename Label>
class CustomCostModelJSON {
private:
    const label::LabelDictionary<Label>& ld_;
    static constexpr double MAX_COST = 1e9;
    std::unordered_set<std::string> important_labels_; 
    
public:
    explicit CustomCostModelJSON(const label::LabelDictionary<Label>& ld) : ld_(ld) {
    }

    double ren(const int label_id_1, const int label_id_2) const {
        // Keep type checking like the original
        if (ld_.get(label_id_1).get_type() != ld_.get(label_id_2).get_type())
            return MAX_COST;

        // Same labels = no cost
        if (ld_.get(label_id_1).get_label().compare(ld_.get(label_id_2).get_label()) == 0)
            return 0.0;

        std::string s1 = ld_.get(label_id_1).get_label();
        std::string s2 = ld_.get(label_id_2).get_label();
        
        // Normal rename cost for non-important labels
        return 0.5 + normalized_levenshtein(s1, s2);
    }

    double del(const int) const {
        return 1.0;
    }

    double ins(const int) const {
        return 1.0;
    }

private:

    double normalized_levenshtein(const std::string& s1, const std::string& s2) const {
        int len1 = s1.length();
        int len2 = s2.length();
        
        if (len1 == 0) return len2 > 0 ? 1.0 : 0.0;
        if (len2 == 0) return 1.0;
        
        std::vector<std::vector<int>> dp(len1 + 1, std::vector<int>(len2 + 1));
        
        for (int i = 0; i <= len1; i++) dp[i][0] = i;
        for (int j = 0; j <= len2; j++) dp[0][j] = j;
        
        for (int i = 1; i <= len1; i++) {
            for (int j = 1; j <= len2; j++) {
                int cost = (s1[i-1] == s2[j-1]) ? 0 : 1;
                dp[i][j] = std::min({
                    dp[i-1][j] + 1,
                    dp[i][j-1] + 1,
                    dp[i-1][j-1] + cost
                });
            }
        }
        
        return (double)dp[len1][len2] / std::max(len1, len2);
    }
};

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

std::vector<std::vector<std::string>> prepare_json_documents(std::vector<std::string>& json_files)
{
    std::vector<std::vector<std::string>> prepared_documents;
    
    for (const std::string& json_string : json_files) {
        // first is file path, second is json as string
        cJSON* root = cJSON_Parse(json_string.c_str());
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

std::vector<std::vector<ComponentId>> n_way_match_pivot(std::vector<std::vector<std::string>>& documents, double cost_thresh)
/*
    Matches components with a pivot document to all other components from the other documents
        args: List of documents, where each document is represented as a list of components
        returns: List of connected components (list of componentId)
*/
{
    // Definitions
    using Label = label::JSONLabel;
    using LabelSetElem = label_set_converter_index::LabelSetElement;
    using CostModel = CustomCostModelJSON<Label>;
    // using CostModel = cost_model::UnitCostModelJSON<Label>;
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
                    if (Cost(i, j) > cost_thresh) {
                        continue;
                    }
                    Match m{pivot_index, k, i, j, Cost(i, j)};
                    matching.push_back(m);
                }
            }
        }
    }

    std::vector<std::vector<ComponentId>> united_matches = buildComponentChains(matching);

    return united_matches;
}

std::vector<std::vector<ComponentId>> n_way_match_all(std::vector<std::vector<std::string>>& documents, double cost_thresh)
/*
    Matches components from all documents to all other components from the other documents
        args: List of documents, where each document is represented as a list of components
        returns: List of connected components (list of componentId)
*/
{ 
    // Definitions
    using Label = label::JSONLabel;
    using LabelSetElem = label_set_converter_index::LabelSetElement;
    // using CostModel = CustomCostModelJSON<Label>;
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
    
    int nr_documents = documents.size();
    
    // p : pivot document index
    // k : document index
    // i : pivot components entry index
    // j : current document components entry index
    std::vector<Match> matching;
    
    for (int p = 0; p < nr_documents; p++) {
        int pivot_index = p;
        std::vector<std::string>& pivot_document = documents[p];
        int pivot_size = pivot_document.size();

        for (int k = 0; k < nr_documents; k++) {
            if (k == p) continue; 

            std::vector<std::string>& target_document = documents[k];
            int target_size = target_document.size();
            int n = std::max(target_size, pivot_size);
            Eigen::MatrixXd Cost = Eigen::MatrixXd::Constant(n, n, MaxCost);
    
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
    
            for (int i = 0; i < n; i++) {
                for (int j = 0; j < n; j++) {
                    solver.AddArcWithCost(i, j, Cost(i,j));
                }
            }
            
            // optimise for best matching
            operations_research::SimpleLinearSumAssignment::Status status = solver.Solve();
            if (status == operations_research::SimpleLinearSumAssignment::OPTIMAL) {
                for (int i = 0; i < pivot_size; i++) {
                    int j = solver.RightMate(i);
                    if (j >= 0 && j < target_size) {
                        if (Cost(i, j) < MaxCost) {
                            if (Cost(i, j) > cost_thresh) {
                                continue;
                            }
                            Match m{p, k, i, j, Cost(i, j)};
                            matching.push_back(m);
                        }
                    }
                }
            }
        }
    }

    std::vector<std::vector<ComponentId>> united_matches = buildComponentChains(matching);

    return united_matches;
}