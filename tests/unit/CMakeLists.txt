add_executable(chat-unit-tests
    main.cc
    ai_gateway_test.cc
    device_trust_test.cc
    errors_test.cc
    leaky_gate_test.cc
    login_limiter_test.cc
    message_rate_limiter_test.cc
    password_policy_test.cc
    payload_crypto_test.cc
    room_management_test.cc
    room_worker_pool_test.cc
    run_permit_test.cc
    session_watcher_test.cc
    table_templates_test.cc
    trace_test.cc
    util_test.cc)
target_link_libraries(chat-unit-tests PRIVATE chat-core)
# Run from the source root, where data/common-passwords.txt lives.
add_test(NAME chat-unit-tests COMMAND chat-unit-tests
         WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})

# Explicitly invoked against disposable MySQL/Redis, never part of ctest.
add_executable(buffering-component-probe ../buffering/component_probe.cc)
target_link_libraries(buffering-component-probe PRIVATE chat-core)

add_test(NAME paused-ai-chunk
         COMMAND buffering-component-probe paused-ai-chunk)
