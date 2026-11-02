add_executable(chat-unit-tests main.cc room_worker_pool_test.cc util_test.cc)
target_link_libraries(chat-unit-tests PRIVATE chat-core)
add_test(NAME chat-unit-tests COMMAND chat-unit-tests)
