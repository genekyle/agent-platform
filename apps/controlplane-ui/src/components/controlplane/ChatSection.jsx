export function ChatSection() {
  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Chat / Command Center</h2>
          <p>Future intent-driven planning surface for tasks and domain actions.</p>
        </div>
      </div>

      <div className="chat-shell">
        <div className="chat-prompt">
          Check Marketplace messages and draft replies for unread buyers.
        </div>

        <div className="chat-card">
          <div className="chat-card-title">I understood this as:</div>
          <div className="chat-rows">
            <div><strong>Domain:</strong> Marketplace</div>
            <div><strong>Objective:</strong> Review unread buyer messages</div>
            <div><strong>Output:</strong> Draft responses requiring approval</div>
            <div><strong>Worker Type:</strong> Desktop seat</div>
          </div>

          <div className="chat-actions">
            <button className="primary-btn">Confirm Task</button>
            <button className="secondary-btn">Edit Intent</button>
            <button className="secondary-btn">Save as Routine</button>
          </div>
        </div>
      </div>
    </section>
  );
}
