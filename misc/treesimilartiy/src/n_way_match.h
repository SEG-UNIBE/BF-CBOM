#pragma once
#include <vector>
#include <string>

struct Match {
    int query_doc;
    int target_doc;
    std::string query_file;
    std::string target_file;
    int query_comp; // index component array
    int target_comp; // index component array
    double cost;
};

std::string read_file(const std::string& filename);

std::vector<std::pair<std::string, std::string>> get_json_files(const std::string& directory_path);

std::vector<std::vector<std::string>> prepare_json_documents(std::vector<std::pair<std::string, std::string>>& json_files);

std::vector<Match> n_way_match(std::string& json_directory);

std::vector<Match> n_way_match(std::vector<std::string>& json_documents);

std::vector<Match> n_way_match(std::vector<std::pair<std::string, std::string>>& json_documents);