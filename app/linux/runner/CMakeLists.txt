cmake_minimum_required(VERSION 3.13)
project(runner LANGUAGES CXX)

# Define the application target. To change its name, change BINARY_NAME in the
# top-level CMakeLists.txt, not the value here, or `flutter run` will no longer
# work.
#
# Any new source files that you add to the application should be added here.
add_executable(${BINARY_NAME}
  "main.cc"
  "my_application.cc"
  "${FLUTTER_MANAGED_DIR}/generated_plugin_registrant.cc"
)

# Apply the standard set of build settings. This can be removed for applications
# that need different build settings.
apply_standard_settings(${BINARY_NAME})

# Harden the native launcher. Provider keys themselves remain in the desktop
# Secret Service through flutter_secure_storage, never in the executable.
target_compile_options(${BINARY_NAME} PRIVATE
  "$<$<NOT:$<CONFIG:Debug>>:-fstack-protector-strong>"
  "$<$<NOT:$<CONFIG:Debug>>:-D_FORTIFY_SOURCE=2>"
  "$<$<NOT:$<CONFIG:Debug>>:-D_GLIBCXX_ASSERTIONS>"
)
target_link_options(${BINARY_NAME} PRIVATE
  "$<$<NOT:$<CONFIG:Debug>>:-Wl,-z,relro,-z,now,-z,noexecstack>"
)

# Add preprocessor definitions for the application ID.
add_definitions(-DAPPLICATION_ID="${APPLICATION_ID}")

# Add dependency libraries. Add any application-specific dependencies here.
target_link_libraries(${BINARY_NAME} PRIVATE flutter)
target_link_libraries(${BINARY_NAME} PRIVATE PkgConfig::GTK)

target_include_directories(${BINARY_NAME} PRIVATE "${CMAKE_SOURCE_DIR}")
