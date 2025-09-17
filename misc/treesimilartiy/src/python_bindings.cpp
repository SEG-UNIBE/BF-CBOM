#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "n_way_match.h"

namespace py = pybind11;

PYBIND11_MODULE(json_matching, m) {
    m.doc() = "Python bindings for tree-similarity";

    py::class_<Match>(m, "Match")
        .def_readonly("query_doc", &Match::query_doc)
        .def_readonly("target_doc", &Match::target_doc)
        .def_readonly("query_comp", &Match::query_comp)
        .def_readonly("query_file", &Match::query_file)
        .def_readonly("target_file", &Match::target_file)
        .def_readonly("target_comp", &Match::target_comp)
        .def_readonly("cost", &Match::cost);

    // simple wrapper: accept a Python list of strings, pass it to n_way_match
    m.def("n_way_match",
          [](std::string json_directory) {
              return n_way_match(json_directory);
          },
          py::arg("json_directory"),
          "Compute best matches and return a list of Match");

    m.def("n_way_match",
          [](std::vector<std::string> json_documents) {
              return n_way_match(json_documents);
          },
          py::arg("json_documents"),
          "Compute best matches from list of JSON strings");
}