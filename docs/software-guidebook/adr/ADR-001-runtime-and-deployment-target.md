# ADR-001: Runtime and Deployment Target

## Status

Accepted

## Date

2026-11-02

## Decision Drivers

- The runtime and its dependencies must work on Linux AMD64, the required target platform.
- The project must deliver a controlled local demonstration.
- Deployment must run on an existing virtual machine; no new infrastructure spending is needed.
- Another developer must be able to reproduce the build and runtime environment after handover.
- The setup must be manageable by one developer within the fixed project window.
- Studio is deployed as a series of docker containers, so the runtime must be compatible with containers.
- Prefer development and deployment styles for the HAN services that are as similar as practical to Studio's Docker Compose setup.

No quality attribute has been identified as a driver for this decision.

## Context

The problem is to provide a controlled environment in which a developer can run Studio and the planned HAN services together and exercise the complete demonstration flow.
This ADR must determine how the services and their dependencies are built, packaged, and run on the existing virtual machine.

Reproducibility of both builds and runtime is a force in this choice.
When the project is handed over, another developer must be able to recreate the same environment to avoid spending time debugging differences in an application unfamiliar to them.
Production rollout is outside the current delivery scope.
This choice does not change how Studio is deployed.

## Options Considered

### Local Process

Run the HAN services directly as host processes on the virtual machine alongside the existing Studio containers.

Pros:

- Work directly with tools on the virtual machine for starting and debugging HAN services.

Cons:

- Host dependencies and their cleanup need configuration management to make the environment reproducible after handover.
- Host dependency updates may break the HAN services, requiring debugging and fixing the services to work with the new versions.
- Running HAN services as host processes creates a separate development and deployment workflow from Studio's Docker Compose setup.

### Containers

Package the HAN services as containers, managed alongside Studio.

Pros:

- Images encapsulate runtime dependencies, helping another developer reproduce the environment after handover.
- Containers align HAN's development and deployment workflow with Studio's and can start the services together for the local demonstration.

Cons:

- Container runtime overhead consumes some resources on the existing virtual machine.
- Building, versioning, and cleaning up images add work.

## Decision

Package and run the HAN services in containers on the existing Linux AMD64 virtual machine.

## Consequences

**Positive:**

- Each service can carry its runtime dependencies in an image, reducing direct coupling to packages installed on the virtual machine.
- Versioned images and deployment configuration can make the environment easier to reproduce and restore after handover.
- Container configuration can define startup behavior and resource limits for the demonstration.

**Negative:**

- Images and their base dependencies must be built, stored, updated, and cleaned up.
- Debugging and persistent data management require container-aware tooling and configuration.
