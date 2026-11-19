<?php
declare(strict_types=1);

function respond(int $status, array $payload): void
{
    echo json_encode(
        ['status' => $status] + $payload,
        JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_THROW_ON_ERROR
    );
    exit;
}

function loadMessages(PDO $database, $after): array
{
    if ($after === null) {
        $rows = $database->query(
            'SELECT m.id, m.user_id, u.username, m.body, m.created_at
             FROM chat_messages AS m
             JOIN users AS u ON u.id = m.user_id
             ORDER BY m.id DESC
             LIMIT 100'
        )->fetchAll();
        $rows = array_reverse($rows);
    } else {
        if (
            !is_string($after)
            || !preg_match('/\A[0-9]{1,19}\z/', $after)
            || (strlen($after) === 19 && strcmp($after, '9223372036854775807') > 0)
        ) {
            respond(400, ['error' => 'O marcador de mensagens é inválido.']);
        }
        $statement = $database->prepare(
            'SELECT m.id, m.user_id, u.username, m.body, m.created_at
             FROM chat_messages AS m
             JOIN users AS u ON u.id = m.user_id
             WHERE m.id > :after
             ORDER BY m.id
             LIMIT 100'
        );
        $statement->execute(['after' => (int) $after]);
        $rows = $statement->fetchAll();
    }

    foreach ($rows as &$row) {
        $row['id'] = (int) $row['id'];
        $row['user_id'] = (int) $row['user_id'];
    }
    unset($row);
    return $rows;
}

try {
    $request = json_decode(
        file_get_contents('php://stdin'),
        true,
        512,
        JSON_THROW_ON_ERROR
    );
    if (!is_array($request)) {
        respond(400, ['error' => 'Solicitação inválida.']);
    }

    $databasePath = getenv('CHAT_DATABASE_PATH');
    if ($databasePath === false || $databasePath === '') {
        throw new RuntimeException('CHAT_DATABASE_PATH não foi configurado.');
    }
    $database = new PDO(
        'sqlite:' . $databasePath,
        null,
        null,
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            PDO::ATTR_TIMEOUT => 5,
        ]
    );

    if (($request['action'] ?? null) === 'list') {
        respond(200, ['messages' => loadMessages($database, $request['after'] ?? null)]);
    }

    if (($request['action'] ?? null) === 'delete') {
        $messageId = $request['message_id'] ?? null;
        $userId = $request['user_id'] ?? null;
        $isAdmin = $request['is_admin'] ?? null;
        if (
            !is_int($messageId)
            || $messageId < 1
            || !is_int($userId)
            || $userId < 1
            || !is_bool($isAdmin)
        ) {
            respond(400, ['error' => 'Solicitação de exclusão inválida.']);
        }

        $database->beginTransaction();
        $statement = $database->prepare(
            'SELECT user_id FROM chat_messages WHERE id = :id'
        );
        $statement->execute(['id' => $messageId]);
        $ownerId = $statement->fetchColumn();
        if ($ownerId === false) {
            $database->rollBack();
            respond(404, ['error' => 'A mensagem não foi encontrada.']);
        }
        if ((int) $ownerId !== $userId && !$isAdmin) {
            $database->rollBack();
            respond(403, ['error' => 'Você só pode excluir suas próprias mensagens.']);
        }

        $statement = $database->prepare(
            'DELETE FROM chat_messages WHERE id = :id'
        );
        $statement->execute(['id' => $messageId]);
        $database->commit();
        respond(200, ['deleted' => true, 'id' => $messageId]);
    }

    if (($request['action'] ?? null) !== 'send') {
        respond(400, ['error' => 'Ação de chat inválida.']);
    }
    $body = $request['body'] ?? null;
    if (!is_string($body)) {
        respond(400, ['error' => 'Escreva uma mensagem antes de enviar.']);
    }
    $body = trim($body);
    if ($body === '') {
        respond(400, ['error' => 'A mensagem não pode ficar vazia.']);
    }
    $length = preg_match_all('/./us', $body, $characters);
    if ($length === false) {
        respond(400, ['error' => 'A mensagem contém caracteres inválidos.']);
    }
    if ($length > 1000) {
        respond(400, ['error' => 'A mensagem deve ter no máximo 1000 caracteres.']);
    }

    $userId = $request['user_id'] ?? null;
    if (!is_int($userId) || $userId < 1) {
        respond(400, ['error' => 'Usuário inválido.']);
    }
    $createdAt = (new DateTimeImmutable('now', new DateTimeZone('UTC')))
        ->format('Y-m-d\TH:i:s.uP');
    $database->beginTransaction();
    $statement = $database->prepare(
        'INSERT INTO chat_messages (user_id, body, created_at)
         VALUES (:user_id, :body, :created_at)'
    );
    $statement->execute([
        'user_id' => $userId,
        'body' => $body,
        'created_at' => $createdAt,
    ]);
    $messageId = (int) $database->lastInsertId();
    $database->commit();

    $statement = $database->prepare(
        'SELECT m.id, m.user_id, u.username, m.body, m.created_at
         FROM chat_messages AS m
         JOIN users AS u ON u.id = m.user_id
         WHERE m.id = :id'
    );
    $statement->execute(['id' => $messageId]);
    $message = $statement->fetch();
    if ($message === false) {
        throw new RuntimeException('A mensagem recém-gravada não foi encontrada.');
    }
    $message['id'] = (int) $message['id'];
    $message['user_id'] = (int) $message['user_id'];
    respond(201, ['message' => $message]);
} catch (JsonException $error) {
    fwrite(STDERR, 'JSON inválido no chat: ' . $error->getMessage() . PHP_EOL);
    respond(400, ['error' => 'Solicitação inválida.']);
} catch (Throwable $error) {
    fwrite(STDERR, 'Erro ao processar o chat: ' . $error->getMessage() . PHP_EOL);
    respond(500, ['error' => 'Não foi possível processar o chat agora.']);
}
