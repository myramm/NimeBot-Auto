import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple, List

RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"

def execute_graphql(token: str, query: str, variables: Optional[Dict[str, Any]] = None, timeout: int = 15) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.strip()}",
        "User-Agent": "railway-cli/5.71.0"
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(RAILWAY_GRAPHQL_URL, data=data_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_body = response.read().decode("utf-8")
            return json.loads(res_body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            return json.loads(error_body)
        except Exception:
            return {"errors": [{"message": f"HTTP {e.code}: {e.reason}"}]}
    except Exception as e:
        return {"errors": [{"message": str(e)}]}

def verify_railway_token(token: str) -> Optional[Dict[str, Any]]:
    query = """
    query {
        me {
            id
            email
            name
        }
    }
    """
    res = execute_graphql(token, query)
    if "data" in res and res["data"] and "me" in res["data"] and res["data"]["me"]:
        return res["data"]["me"]
    return None

def get_project_details(token: str, project_id: str) -> Optional[Dict[str, Any]]:
    query = f"""
    query {{
        project(id: "{project_id}") {{
            id
            name
            environments {{
                edges {{
                    node {{
                        id
                        name
                    }}
                }}
            }}
            services {{
                edges {{
                    node {{
                        id
                        name
                    }}
                }}
            }}
        }}
    }}
    """
    res = execute_graphql(token, query)
    try:
        return res["data"]["project"]
    except Exception:
        return None

def list_projects(token: str) -> List[Dict[str, Any]]:
    query = """
    query {
        me {
            workspaces {
                projects {
                    edges {
                        node {
                            id
                            name
                        }
                    }
                }
            }
            projects {
                edges {
                    node {
                        id
                        name
                    }
                }
            }
        }
    }
    """
    res = execute_graphql(token, query)
    proj_ids = []
    
    try:
        # Check workspaces
        workspaces = res.get("data", {}).get("me", {}).get("workspaces", [])
        for ws in workspaces:
            for edge in ws.get("projects", {}).get("edges", []):
                p_id = edge.get("node", {}).get("id")
                if p_id and p_id not in proj_ids:
                    proj_ids.append(p_id)
        
        # Check personal projects
        for edge in res.get("data", {}).get("me", {}).get("projects", {}).get("edges", []):
            p_id = edge.get("node", {}).get("id")
            if p_id and p_id not in proj_ids:
                proj_ids.append(p_id)
    except Exception:
        pass

    # Check top-level projects if returned (e.g. from service token or mock)
    top_edges = res.get("data", {}).get("projects", {}).get("edges", [])
    if top_edges:
        top_projects = [e["node"] for e in top_edges if "node" in e]
        if top_projects and "services" in top_projects[0]:
            return top_projects
        for e in top_edges:
            pid = e.get("node", {}).get("id")
            if pid and pid not in proj_ids:
                proj_ids.append(pid)
    results = []
    for pid in proj_ids:
        details = get_project_details(token, pid)
        if details:
            results.append(details)
    return results

def ensure_railway_project(token: str, project_name: str = "file-streamer") -> Tuple[str, str, str]:
    """
    Finds existing project, or finds service 'uploader' or creates/identifies ids.
    Returns (project_id, environment_id, service_id)
    """
    projects = list_projects(token)
    target_project = None
    for p in projects:
        if p["name"] == project_name:
            target_project = p
            break
    
    if not target_project and projects:
        target_project = projects[0]
        
    if not target_project:
        # Create new project if none found
        create_query = f"""
        mutation {{
            projectCreate(input: {{ name: "{project_name}" }}) {{
                id
                name
            }}
        }}
        """
        res = execute_graphql(token, create_query)
        p_id = res.get("data", {}).get("projectCreate", {}).get("id")
        if p_id:
            target_project = get_project_details(token, p_id)
        if not target_project:
            raise RuntimeError(f"Failed to provision Railway project {project_name}")

    proj_id = target_project["id"]
    env_edges = target_project.get("environments", {}).get("edges", [])
    env_id = env_edges[0]["node"]["id"] if env_edges else ""
    
    srv_edges = target_project.get("services", {}).get("edges", [])
    srv_id = ""
    for s in srv_edges:
        if s["node"]["name"] == "uploader":
            srv_id = s["node"]["id"]
            break
    if not srv_id and srv_edges:
        srv_id = srv_edges[0]["node"]["id"]
        
    return proj_id, env_id, srv_id

def set_worker_start_command(token: str, env_id: str, service_id: str, command: str) -> bool:
    query = """
    mutation serviceInstanceUpdate($environmentId: String!, $serviceId: String!, $input: ServiceInstanceUpdateInput!) {
        serviceInstanceUpdate(environmentId: $environmentId, serviceId: $serviceId, input: $input)
    }
    """
    res = execute_graphql(token, query, {
        "environmentId": env_id,
        "serviceId": service_id,
        "input": {"startCommand": command}
    })
    return bool(res.get("data", {}).get("serviceInstanceUpdate"))

def redeploy_worker(token: str, env_id: str, service_id: str) -> bool:
    query = """
    mutation serviceInstanceRedeploy($environmentId: String!, $serviceId: String!) {
        serviceInstanceRedeploy(environmentId: $environmentId, serviceId: $serviceId)
    }
    """
    res = execute_graphql(token, query, {
        "environmentId": env_id,
        "serviceId": service_id
    })
    return bool(res.get("data", {}).get("serviceInstanceRedeploy"))

def stop_worker(token: str, deployment_id: str) -> bool:
    query = """
    mutation deploymentRemove($id: String!) {
        deploymentRemove(id: $id)
    }
    """
    res = execute_graphql(token, query, {"id": deployment_id})
    return bool(res.get("data", {}).get("deploymentRemove"))

def get_latest_deployment(token: str, service_id: str, env_id: str) -> Optional[Dict[str, Any]]:
    query = f"""
    query {{
        service(id: "{service_id}") {{
            deployments(first: 1) {{
                edges {{
                    node {{
                        id
                        status
                        createdAt
                    }}
                }}
            }}
        }}
    }}
    """
    res = execute_graphql(token, query)
    try:
        edges = res["data"]["service"]["deployments"]["edges"]
        if edges:
            return edges[0]["node"]
    except Exception:
        pass
    return None
