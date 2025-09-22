#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "n_way_match.h"

namespace py = pybind11;

PYBIND11_MODULE(json_matching, m) {
    m.doc() = "Python bindings for tree-similarity";

    py::class_<ComponentId>(m, "ComponentId")
        .def_readonly("doc_id", &ComponentId::doc_id)
        .def_readonly("comp_id", &ComponentId::comp_id)
        .def_readonly("cost", &ComponentId::cost);

        
    m.def("n_way_match_pivot",
        [](std::vector<std::vector<std::string>>& documents, double cost_thresh) {
            return n_way_match_pivot(documents, cost_thresh);
        },
        py::arg("documents"), 
        py::arg("cost_thresh") = 25.0,
        "Match components using pivot strategy");
        
    m.def("n_way_match_all",
          [](std::vector<std::vector<std::string>> json_documents, double cost_thresh) {
              return n_way_match_all(json_documents, cost_thresh);
          },
          py::arg("json_documents"),
          py::arg("cost_thresh") = 25.0,
          "Match components using all-to-all strategy");

    m.def("prepare_json_documents",
          [](std::vector<std::string> json_files) {
              return prepare_json_documents(json_files);
          },
          py::arg("json_files"),
          "Extracts all components from the cbom json files and returns a list of lists containing all components");

}