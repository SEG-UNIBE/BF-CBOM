cmake_minimum_required(VERSION 3.12)
project(TreeSimilarityApp LANGUAGES C CXX)

# Standards
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Compiler flags
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -Wall")

# External dependencies
add_subdirectory(external/tree-similarity)
add_subdirectory(external/cJSON)

# OR-Tools (optional)
set(ORTOOLS_DIR "${CMAKE_SOURCE_DIR}/external/or-tools_arm64_macOS-15.3.1_cpp_v9.12.4544")
if(EXISTS "${ORTOOLS_DIR}/lib/libortools.9.12.dylib")
    add_library(ortools::ortools UNKNOWN IMPORTED)
    set_target_properties(ortools::ortools PROPERTIES
        IMPORTED_LOCATION "${ORTOOLS_DIR}/lib/libortools.9.12.dylib"
        INTERFACE_INCLUDE_DIRECTORIES "${ORTOOLS_DIR}/include"
    )
    set(ORTOOLS_TARGET "ortools::ortools")
endif()

# Try to locate OR-Tools installed via CMake package
find_package(ortools CONFIG)
if(ortools_FOUND)
    set(ORTOOLS_TARGET ortools::ortools)
    set(ORTOOLS_INCLUDE_DIRS "${ortools_DIR}/../include")
endif()

# Pybind11 for Python bindings
include(FetchContent)
FetchContent_Declare(pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG v2.11.1
)
FetchContent_MakeAvailable(pybind11)

# Core library containing n_way_match implementation
add_library(tree_similarity_core STATIC src/n_way_match.cpp)
target_include_directories(tree_similarity_core PUBLIC
    src
    external/tree-similarity/src
    external/cJSON
    external/eigen
)
target_link_libraries(tree_similarity_core PRIVATE
    TreeSimilarity
    cjson
)
if(DEFINED ORTOOLS_TARGET)
    target_link_libraries(tree_similarity_core PRIVATE ${ORTOOLS_TARGET})
    if(NOT ORTOOLS_INCLUDE_DIRS)
        get_target_property(ORTOOLS_INCLUDE_DIRS ortools::ortools INTERFACE_INCLUDE_DIRECTORIES)
    endif()
    if(ORTOOLS_INCLUDE_DIRS)
        target_include_directories(tree_similarity_core PRIVATE ${ORTOOLS_INCLUDE_DIRS})
    endif()
else()
    message(WARNING "ORTools not found; building without assignment solver support")
endif()

# Python extension
pybind11_add_module(json_matching src/python_bindings.cpp)
target_link_libraries(json_matching PRIVATE tree_similarity_core)

# Main executable
add_executable(main src/main.cpp)
target_include_directories(main PRIVATE src external/cJSON external/eigen)
target_link_libraries(main PRIVATE tree_similarity_core)
if(DEFINED ORTOOLS_TARGET)
    if(NOT ORTOOLS_INCLUDE_DIRS)
        get_target_property(ORTOOLS_INCLUDE_DIRS ortools::ortools INTERFACE_INCLUDE_DIRECTORIES)
    endif()
    if(ORTOOLS_INCLUDE_DIRS)
        target_include_directories(main PRIVATE ${ORTOOLS_INCLUDE_DIRS})
    endif()
endif()

# Output directory
set_target_properties(main PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
)
