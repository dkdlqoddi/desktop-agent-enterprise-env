package dev.privateagent.android.data

import android.database.SQLException
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteFullException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Why the app's database could not be read or written: the phone's storage is full, or anything else. */
enum class StoreProblem { FULL, FAILED }

/**
 * A database error, as the stores give it in place of the platform's [SQLException]: the storage is full, or the
 * database could not be opened, read or written. It is a failure the app tells of and goes on from, never one that
 * ends it: whoever ran the store's work shows its sentence (Texts.storage).
 */
class StoreException(val problem: StoreProblem, cause: Throwable) : Exception("Database error: $problem", cause)

/** A store's database work, off the main thread: a database error comes out of it as a [StoreException]. */
internal suspend fun <T> withDatabase(work: () -> T): T = withContext(Dispatchers.IO) {
    try {
        work()
    } catch (error: SQLException) {
        throw StoreException(if (error is SQLiteFullException) StoreProblem.FULL else StoreProblem.FAILED, error)
    }
}

/**
 * [work] in one transaction. When the work fails, ending the transaction must not hide why: on a full disk SQLite has
 * already rolled it back, and the rollback that ending it asks for fails as well.
 */
internal inline fun <T> SQLiteDatabase.inTransaction(work: () -> T): T {
    beginTransaction()
    val result = try {
        work().also { setTransactionSuccessful() }
    } catch (failure: Throwable) {
        try {
            endTransaction()
        } catch (rollback: SQLException) {
            failure.addSuppressed(rollback)
        }
        throw failure
    }
    // A commit that fails is the work's failure.
    endTransaction()
    return result
}
