// Semgrep rule test fixture (annotations: "ruleid" must match, "ok" must not).
// Test data only; never compiled or executed.
import java.io.ObjectInputStream;
import java.security.MessageDigest;
import java.sql.Connection;
import java.sql.Statement;

class Fixture {
    void sql(Statement stmt, Connection conn, String id) throws Exception {
        // ruleid: java.sql-injection.string-built-query
        stmt.executeQuery("SELECT * FROM users WHERE id = " + id);
        // ruleid: java.sql-injection.string-built-query
        conn.prepareStatement("DELETE FROM t WHERE id = " + id);
        // ok: java.sql-injection.string-built-query
        conn.prepareStatement("SELECT * FROM users WHERE id = ?");
        String q = "UPDATE users SET name = '" + id + "'";
        // ruleid: java.sql-injection.string-built-query
        stmt.executeUpdate(q);
    }

    void cmd(String arg) throws Exception {
        // ruleid: java.command-injection.runtime-exec
        Runtime.getRuntime().exec("ping " + arg);
        // ok: java.command-injection.runtime-exec
        Runtime.getRuntime().exec("uptime");
    }

    void hash() throws Exception {
        // ruleid: java.crypto.weak-hash
        MessageDigest.getInstance("MD5");
        // ok: java.crypto.weak-hash
        MessageDigest.getInstance("SHA-256");
    }

    Object read(ObjectInputStream in) throws Exception {
        // ruleid: java.deserialization.object-input-stream
        return in.readObject();
    }

    void security(HttpSecurity http) throws Exception {
        // ruleid: java.spring.csrf-disabled
        http.csrf().disable();
    }

    // ruleid: java.secrets.hardcoded-credential
    private String dbPassword = "Sup3rS3cretValue";
    // ok: java.secrets.hardcoded-credential
    private String apiKey = System.getenv("API_KEY");
}
