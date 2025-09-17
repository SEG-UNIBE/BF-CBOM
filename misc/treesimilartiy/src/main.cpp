#include <iostream>
#include <fstream>
#include <unistd.h>
#include <vector>
#include <filesystem>
#include "n_way_match.h"

namespace fs = std::filesystem;

int main(int argc, char* argv[]) {
    std::cout << "Running json matching algorithm" << std::endl;

    /*
        Input: a list of documents each consists of a set of json files/objects
        Compute: Pivot-based optimisation starts with the largest document and compares against that to get the best matching
    */

    if (argc != 2) {
        std::cerr << "Usage: " << argv[0] << " <path_to_json_directory>" << std::endl;
        return 1;
    }
    std::string json_directory = argv[1];
    std::cout << "Looking for JSON files in: " << json_directory << std::endl;
    
    // Check if directory exists
    if (!fs::exists(json_directory) || !fs::is_directory(json_directory)) {
        std::cerr << "Error: " << json_directory << " is not a valid directory" << std::endl;
        return 1;
    }
    std::vector<std::pair<std::string,std::string>> json_files = get_json_files(json_directory);
    
    if (json_files.empty()) {
        std::cout << "No JSON files found in " << json_directory << std::endl;
        return 0;
    }
    
    std::cout << "Found " << json_files.size() << " JSON files" << std::endl;

    // Process the JSON files
    n_way_match(json_files);

    return 0;
}
