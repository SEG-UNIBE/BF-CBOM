## How to build


### Dependencies

Download all dependencies into the `external/` folder
```
https://gitlab.com/libeigen/eigen.git
https://github.com/DaveGamble/cJSON.git
https://github.com/DatabaseGroup/tree-similarity.git
```

and download the OR-Tool from google for mac 

```
https://developers.google.com/optimization/install/cpp/binary_mac
```

or for linux

```
https://developers.google.com/optimization/install/cpp/binary_linux
```

and place them into the `external/` folder

### Build

To build the project run

```
mkdir build && cd build
```

then run the `cmake` command
```
cmake ..  -DCMAKE_POLICY_VERSION_MINIMUM=3.5
```

and finally

```
make -j
```

