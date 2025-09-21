
# Improving the Pixell Agent Runtime

This document outlines a plan to improve the Pixell Agent Runtime, moving it from its current single-instance Fargate deployment to a more scalable, resilient, and efficient architecture.

## Current Architecture

The current runtime is a monolithic FastAPI application that loads and runs agent packages (`.apkg` files). It's deployed as a single container on AWS Fargate, which has several limitations:

*   **Scalability:** A single instance can only handle a limited number of agents and requests.
*   **Efficiency:** Running a single agent on a dedicated Fargate instance is not cost-effective.
*   **Resilience:** If the runtime instance fails, all agents running on it will become unavailable.
*   **Flexibility:** The monolithic architecture makes it difficult to update or modify individual agents without redeploying the entire runtime.

## Proposed Architecture: A Multi-Tenant, Microservices-based Approach

To address these limitations, we propose a new architecture based on microservices and container orchestration. In this new model, each agent will run in its own Docker container, managed by a container orchestration platform like Kubernetes.

### Key Components:

1.  **Agent Runtime:** A lightweight, standalone runtime responsible for loading and running a single agent. This will be a stripped-down version of the current runtime, focused solely on executing a single agent's code.
2.  **Agent Gateway:** A new component that acts as the entry point for all incoming requests. It will be responsible for routing requests to the appropriate agent, as well as handling authentication, authorization, and rate limiting.
3.  **Agent Registry:** A central service for managing the lifecycle of agents. It will keep track of all available agents, their versions, and their current status.
4.  **Message Bus:** A message bus like RabbitMQ or Kafka will be used for asynchronous communication between agents.
5.  **Service Mesh:** A service mesh like Istio or Linkerd will be used to manage communication between the different components of the system. It will provide features like service discovery, load balancing, and traffic management.
6.  **Container Orchestrator:** We recommend using Kubernetes to orchestrate the deployment and scaling of the different components.

### Benefits of the New Architecture:

*   **Scalability:** The new architecture will be highly scalable. We can easily add or remove agent instances as needed to meet demand.
*   **Efficiency:** By running each agent in its own container, we can make more efficient use of resources.
*   **Resilience:** The new architecture will be much more resilient. If one agent instance fails, it will not affect the other agents.
*   **Flexibility:** The microservices-based architecture will make it much easier to update or modify individual agents.

## Roadmap

Here's a high-level roadmap for implementing the new architecture:

1.  **Develop the Agent Runtime:** Create a new, lightweight runtime for running individual agents.
2.  **Develop the Agent Gateway:** Create the new Agent Gateway component.
3.  **Set up a Kubernetes Cluster:** Set up a Kubernetes cluster on AWS using a service like EKS.
4.  **Containerize the Components:** Create Docker images for the Agent Runtime, Agent Gateway, and other components.
5.  **Deploy to Kubernetes:** Deploy the new components to the Kubernetes cluster.
6.  **Implement the Message Bus and Service Mesh:** Integrate a message bus and a service mesh into the new architecture.
7.  **Migrate Existing Agents:** Migrate the existing agents to the new runtime.

This new architecture will provide a solid foundation for building a scalable, resilient, and efficient agent platform.
