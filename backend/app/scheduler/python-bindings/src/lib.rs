use proxmox_resource_scheduling::{
    node::NodeStats,
    resource::ResourceStats,
    scheduler::{NodeUsage, Scheduler},
};
use pyo3::{exceptions::PyValueError, prelude::*};

type NodeInput = (String, f64, usize, usize, usize);

#[pyfunction]
fn score_nodes(
    nodes: Vec<NodeInput>,
    resource_maxcpu: f64,
    resource_maxmem: usize,
) -> PyResult<Vec<(String, f64)>> {
    if nodes.is_empty() {
        return Err(PyValueError::new_err("no candidate nodes"));
    }
    if !resource_maxcpu.is_finite() || resource_maxcpu < 0.0 || resource_maxmem == 0 {
        return Err(PyValueError::new_err("invalid VM resource requirements"));
    }

    let mut usages = Vec::with_capacity(nodes.len());
    for (name, cpu, maxcpu, mem, maxmem) in nodes {
        if name.is_empty()
            || !cpu.is_finite()
            || cpu < 0.0
            || maxcpu == 0
            || maxmem == 0
        {
            return Err(PyValueError::new_err(format!(
                "invalid resource metrics for node '{name}'"
            )));
        }
        usages.push(NodeUsage {
            name,
            stats: NodeStats {
                cpu,
                maxcpu,
                mem,
                maxmem,
            },
        });
    }

    Scheduler::from_nodes(usages)
        .score_nodes_to_start_resource(ResourceStats {
            cpu: 0.0,
            maxcpu: resource_maxcpu,
            mem: 0,
            maxmem: resource_maxmem,
        })
        .map_err(|err| PyValueError::new_err(err.to_string()))
}

#[pymodule]
fn _rust_topsis(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(score_nodes, module)?)?;
    Ok(())
}

