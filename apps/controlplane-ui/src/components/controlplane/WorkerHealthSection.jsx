import { getStatusClass } from "./utils";

export function WorkerHealthSection({ workers }) {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Worker Health</h2>
          <p>Worker readiness, seat assignments, and placeholder execution posture.</p>
        </div>
      </div>

      <div className="table-wrap">
        <table className="runs-table">
          <thead>
            <tr>
              <th>Worker ID</th>
              <th>Name</th>
              <th>Domain</th>
              <th>Status</th>
              <th>Seat</th>
            </tr>
          </thead>
          <tbody>
            {workers.map((worker) => (
              <tr key={worker.id}>
                <td className="mono">{worker.id}</td>
                <td>{worker.name}</td>
                <td>{worker.domain}</td>
                <td>
                  <span className={getStatusClass(worker.status)}>{worker.status}</span>
                </td>
                <td>{worker.seat}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
