# Blender Lab parity checklist

The reference is Blender Lab MCP **1.0.3**, tag `v1.0.3`, commit `2cea8d5`. The first qualification environment is Blender **5.3.0** on Linux. These 26 tool names are the observed baseline. Stereograph may use different names if the compatibility record explains how a caller reaches the equivalent workflow. [Blender Lab MCP](https://www.blender.org/lab/mcp-server/) · [Upstream source](https://projects.blender.org/lab/blender_mcp)

## Connection and execution

- [ ] `execute_blender_code`
- [ ] `execute_blender_code_for_cli`

## File summaries

- [ ] `get_blendfile_summary_datablocks`
- [ ] `get_blendfile_summary_datablocks_for_cli`
- [ ] `get_blendfile_summary_missing_files`
- [ ] `get_blendfile_summary_missing_files_for_cli`
- [ ] `get_blendfile_summary_of_linked_libraries`
- [ ] `get_blendfile_summary_of_linked_libraries_for_cli`
- [ ] `get_blendfile_summary_path_info`
- [ ] `get_blendfile_summary_path_info_for_cli`
- [ ] `get_blendfile_summary_usage_guess`
- [ ] `get_blendfile_summary_usage_guess_for_cli`

## Scene and object inspection

- [ ] `get_object_detail_summary`
- [ ] `get_objects_summary`

## Documentation

- [ ] `get_python_api_docs`
- [ ] `search_api_docs`
- [ ] `search_manual_docs`

## Viewport navigation and capture

- [ ] `get_screenshot_of_area_as_image`
- [ ] `get_screenshot_of_window_as_image`
- [ ] `get_screenshot_of_window_as_json`
- [ ] `jump_to_tab_by_name`
- [ ] `jump_to_tab_by_space_type`
- [ ] `jump_to_view3d_object_by_name`
- [ ] `jump_to_view3d_object_data_by_name`
- [ ] `render_thumbnail_to_path`
- [ ] `render_viewport_to_path`

## Evidence

For each item, record the reference and candidate build, Blender version, mode, fixture hash, request, result, and failure behaviour. Include which features require an interactive window and what an unsupported headless call returns. Verify schema and actual Blender state or output independently. Compare semantic output for summaries and images. Any approved deviation needs an explicit compatibility note.
