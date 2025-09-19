#ifndef N_WAY_MATCH_H
#define N_WAY_MATCH_H

#pragma once
#include <vector>
#include <string>
#include <unordered_map>
#include <unordered_set>

struct Match {
    int query_doc;
    int target_doc;
    int query_comp; // index component array
    int target_comp; // index component array
    double cost;
};

struct ComponentId {
    int doc_id;
    int comp_id;
    double cost;
    
    bool operator==(const ComponentId& other) const {
        return doc_id == other.doc_id && comp_id == other.comp_id;
    }
};

// Hash function for ComponentId
struct ComponentIdHash {
    size_t operator()(const ComponentId& id) const {
        size_t h1 = std::hash<int>()(id.doc_id);
        size_t h2 = std::hash<int>()(id.comp_id);
        
        // Boost hash_combine algorithm
        return h1 ^ (h2 + 0x9e3779b9 + (h1 << 6) + (h1 >> 2));
    }
};

class UnionFind {
private:
    std::unordered_map<ComponentId, ComponentId, ComponentIdHash> parent;
    
public:
    ComponentId find(const ComponentId& x);
    void unite(const ComponentId& x, const ComponentId& y);
    std::vector<std::vector<ComponentId>> getConnectedComponents();
};

std::vector<std::vector<ComponentId>> buildComponentChains(const std::vector<Match>& matches);
std::string read_file(const std::string& filename);
std::vector<std::pair<std::string, std::string>> get_json_files(const std::string& directory_path);
std::vector<std::vector<std::string>> prepare_json_documents(std::vector<std::string>& json_files);

std::vector<std::vector<ComponentId>> n_way_match_pivot(std::vector<std::vector<std::string>>& documents, double cost_thresh = 25.0);
std::vector<std::vector<ComponentId>> n_way_match_all(std::vector<std::vector<std::string>>& documents, double cost_thresh = 25.0);

#endif // N_WAY_MATCH_H