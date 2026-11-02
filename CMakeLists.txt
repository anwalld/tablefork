cmake_minimum_required(VERSION 3.20)
project(CppAiServer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(CHAT_BUILD_TESTS "Build unit tests" OFF)

find_package(Drogon CONFIG REQUIRED)

file(GLOB_RECURSE CHAT_SOURCES CONFIGURE_DEPENDS src/*.cc)
list(REMOVE_ITEM CHAT_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/src/main.cc)

add_library(chat-core STATIC ${CHAT_SOURCES})
target_include_directories(chat-core PUBLIC src)
target_link_libraries(chat-core PUBLIC Drogon::Drogon)

# Controllers register themselves through static initializers, so the whole
# library must be linked in.
add_executable(chat-server src/main.cc)
target_link_libraries(chat-server PRIVATE
    -Wl,--whole-archive chat-core -Wl,--no-whole-archive)

if(CHAT_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests/unit)
endif()
