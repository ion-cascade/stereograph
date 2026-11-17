# Security

Use GitHub's private vulnerability reporting for security issues. If it is unavailable, use the [Ion Cascade maintainer contact information](https://github.com/ion-cascade). Include the affected version, Blender session mode and a minimal disposable reproduction.

Blender Python execution can read and change files using the Blender process's permissions. Enable it only for a trusted client and keep the session bridge local. Stereograph's typed tools should require only the capabilities needed for their operation.
