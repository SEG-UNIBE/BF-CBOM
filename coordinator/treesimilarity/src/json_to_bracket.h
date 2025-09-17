#pragma once

#include <string>
#include <vector>
#include <memory>
#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <cJSON.h>
#include <algorithm>
#include <sstream>

class JsonToBracket {
private:
    // Remove ALL whitespace (equivalent to Python's split() and join())
    std::string remove_all_whitespace(const std::string& str) {
        std::string result;
        result.reserve(str.length());
        
        for (char c : str) {
            if (!std::isspace(static_cast<unsigned char>(c))) {
                result += c;
            }
        }
        
        return result;
    }
    
    // Escape brackets - BOTH { and } become \{ (matching Python bug)
    std::string escape_brackets(const std::string& str) {
        std::string escaped;
        escaped.reserve(str.length() * 2);
        
        for (char c : str) {
            if (c == '{') {
                escaped += "\\{";
            } else if ( c == '}') {
                escaped += "\\}";
            } else {
                escaped += c;
            }
        }
        
        return escaped;
    }
    
    // ASCII filter (equivalent to Python's encode("ascii", "ignore").decode())
    std::string ascii_filter(const std::string& str) {
        std::string filtered;
        filtered.reserve(str.length());
        
        for (char c : str) {
            if (static_cast<unsigned char>(c) < 128) {  // ASCII only
                filtered += c;
            }
        }
        
        return filtered;
    }
    
    // Convert JSON to bracket notation (recursive)
    void json_to_bracket_recursive(cJSON* json, std::string& result, bool sort_keys) {
        if (cJSON_IsObject(json)) {
            // OBJECT
            result += "{\\{\\}";  

            if (sort_keys) {
                // Collect and sort keys
                std::vector<std::pair<std::string, cJSON*>> items;
                cJSON* item = nullptr;
                cJSON_ArrayForEach(item, json) {
                    if (item->string) {
                        items.push_back({std::string(item->string), item});
                    }
                }
                
                std::sort(items.begin(), items.end());
                
                for (const auto& pair : items) {
                    std::string key = ascii_filter(pair.first);
                    std::string escaped_key = escape_brackets(key);
                    
                    result += "{\"" + escaped_key + "\":";
                    json_to_bracket_recursive(pair.second, result, sort_keys);
                    result += "}";
                }
            } else {
                cJSON* item = nullptr;
                cJSON_ArrayForEach(item, json) {
                    if (item->string) {
                        std::string key = ascii_filter(std::string(item->string));
                        std::string escaped_key = escape_brackets(key);
                        
                        result += "{\"" + escaped_key + "\":";
                        json_to_bracket_recursive(item, result, sort_keys);
                        result += "}";
                    }
                }
            }
            result += "}";
            
        } else if (cJSON_IsArray(json)) {
            // ARRAY
            result += "{[]";
            
            cJSON* item = nullptr;
            cJSON_ArrayForEach(item, json) {
                json_to_bracket_recursive(item, result, sort_keys);
            }
            result += "}";
            
        } else {
            // VALUE
            if (cJSON_IsString(json)) {
                std::string str_val = cJSON_GetStringValue(json);
                std::string ascii_filtered = ascii_filter(str_val);
                std::string no_spaces = remove_all_whitespace(ascii_filtered);
                std::string escaped = escape_brackets(no_spaces);
                
                result += "{\"" + escaped + "\"}";
                
            } else if (cJSON_IsNumber(json)) {
                double num_val = cJSON_GetNumberValue(json);
                
                if (num_val == static_cast<int>(num_val)) {
                    result += "{" + std::to_string(static_cast<int>(num_val)) + "}";
                } else {
                    // Format float to match Python's str() output
                    std::ostringstream oss;
                    oss << num_val;
                    result += "{" + oss.str() + "}";
                }
                
            } else if (cJSON_IsBool(json)) {
                if (cJSON_IsTrue(json)) {
                    result += "{True}";
                } else {
                    result += "{False}";
                }
                
            } else if (cJSON_IsNull(json)) {
                result += "{null}";
            }
        }
    }

public:
    // Main conversion function
    std::string json_to_bracket(const std::string& json_string, bool sort_keys = false) {
        cJSON* json = cJSON_Parse(json_string.c_str());
        if (json == nullptr) {
            const char* error_ptr = cJSON_GetErrorPtr();
            throw std::runtime_error("Error parsing JSON: " + 
                std::string(error_ptr ? error_ptr : "Unknown error"));
        }
        
        std::string result;
        json_to_bracket_recursive(json, result, sort_keys);
        
        cJSON_Delete(json);
        return result;
    }
    
    // Convert JSON collection to bracket notation
    std::vector<std::string> json_collection_to_bracket(const std::string& json_string, bool sort_keys = false) {
        cJSON* json_array = cJSON_Parse(json_string.c_str());
        if (!cJSON_IsArray(json_array)) {
            cJSON_Delete(json_array);
            throw std::runtime_error("Error: JSON is not an array");
        }
        
        int array_size = cJSON_GetArraySize(json_array);
        std::vector<std::string> results;
        results.reserve(array_size);
        
        for (int i = 0; i < array_size; i++) {
            cJSON* item = cJSON_GetArrayItem(json_array, i);
            std::string result;
            json_to_bracket_recursive(item, result, sort_keys);
            results.push_back(result);
        }
        
        cJSON_Delete(json_array);
        return results;
    }
};

// Convenience function for simple usage
inline std::string json_to_bracket(const std::string& json_string, bool sort_keys = false) {
    JsonToBracket converter;
    return converter.json_to_bracket(json_string, sort_keys);
}