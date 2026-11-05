cmake_minimum_required(VERSION 3.20)
project(CppAiServer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(CHAT_BUILD_TESTS "Build unit tests" OFF)

find_package(Drogon CONFIG REQUIRED)
find_package(Protobuf REQUIRED)
find_package(OpenSSL REQUIRED)
# Ubuntu's gRPC package links against c-ares::cares without defining it.
if(NOT TARGET c-ares::cares)
    find_library(CARES_LIBRARY cares REQUIRED)
    add_library(c-ares::cares UNKNOWN IMPORTED)
    set_target_properties(c-ares::cares PROPERTIES IMPORTED_LOCATION ${CARES_LIBRARY})
endif()
find_package(gRPC CONFIG REQUIRED)

# C++ code for proto/ai.proto, shared with the Python AI service.
set(PROTO_SRC ${CMAKE_CURRENT_SOURCE_DIR}/proto/ai.proto)
set(PROTO_OUT ${CMAKE_CURRENT_BINARY_DIR}/proto)
file(MAKE_DIRECTORY ${PROTO_OUT})
set(PROTO_GENERATED
    ${PROTO_OUT}/ai.pb.cc ${PROTO_OUT}/ai.pb.h
    ${PROTO_OUT}/ai.grpc.pb.cc ${PROTO_OUT}/ai.grpc.pb.h)
add_custom_command(
    OUTPUT ${PROTO_GENERATED}
    COMMAND protobuf::protoc
        --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto
        --cpp_out=${PROTO_OUT}
        --grpc_out=${PROTO_OUT}
        --plugin=protoc-gen-grpc=$<TARGET_FILE:gRPC::grpc_cpp_plugin>
        ${PROTO_SRC}
    DEPENDS ${PROTO_SRC})

file(GLOB_RECURSE CHAT_SOURCES CONFIGURE_DEPENDS src/*.cc)
list(REMOVE_ITEM CHAT_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/src/main.cc)

add_library(chat-core STATIC ${CHAT_SOURCES} ${PROTO_GENERATED})
file(READ ${CMAKE_CURRENT_SOURCE_DIR}/src/buffering/lua/queue.lua QUEUE_LUA)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS src/buffering/lua/queue.lua)
configure_file(src/buffering/queue_script.h.in buffering/queue_script.h @ONLY)
target_include_directories(chat-core PUBLIC src ${PROTO_OUT} ${CMAKE_CURRENT_BINARY_DIR})
target_link_libraries(chat-core PUBLIC Drogon::Drogon gRPC::grpc++ protobuf::libprotobuf OpenSSL::Crypto)

# Controllers register themselves through static initializers, so the whole
# library must be linked in.
add_executable(chat-server src/main.cc)
target_link_libraries(chat-server PRIVATE
    -Wl,--whole-archive chat-core -Wl,--no-whole-archive)

if(CHAT_BUILD_TESTS)
    target_compile_definitions(chat-core PRIVATE CHAT_BUFFER_TEST_HOOKS=1)
    enable_testing()
    add_subdirectory(tests/unit)
endif()
